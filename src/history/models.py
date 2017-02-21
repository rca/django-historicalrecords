import copy
import datetime

from django.db import models
from django.conf import settings
from django.db.models.base import ModelBase
from django.utils import timezone
from functools import wraps

from history import manager


class HistoryChange(object):
    def __init__(self, name, from_value, to_value, verbose_name):
        self.name = name
        self.from_value = from_value
        self.to_value = to_value
        self.verbose_name = verbose_name

    def __unicode__(self):
        return 'Field "%s" changed from "%s" to "%s"' % (self.name, self.from_value, self.to_value)


class HistoricalRecords(object):
    """
    Usage:
    class MyModel(models.Model):
        ...
        history = HistoricalRecords()

    Parameters:
    - (optional) module: act like this model was defined in another module.
                         (This will be reported to Django and South for
                         migrations, and table names.)
    - (optional) fields: a list of field names to be checked and saved. If
                         nothing is defined, all fields will be saved.
    """
    def __init__(self, module=None, fields=None):
        self._module = module
        self._fields = fields

    def contribute_to_class(self, cls, name):
        self.manager_name = name
        models.signals.class_prepared.connect(self.finalize, sender=cls)

    def finalize(self, sender, **kwargs):
        history_model = self.create_history_model(sender)

        # The HistoricalRecords object will be discarded,
        # so the signal handlers can't use weak references.
        models.signals.post_save.connect(self.post_save, sender=sender,
                                         weak=False)
        models.signals.post_delete.connect(self.post_delete, sender=sender,
                                           weak=False)

        descriptor = manager.HistoryDescriptor(history_model, self.get_important_field_names(sender))
        setattr(sender, self.manager_name, descriptor)
        self.capture_save_method(sender)
        self.create_set_editor_method(sender)

    def capture_save_method(self, sender):
        """
        Replace 'save()' by 'save(editor=user)'
        """
        original_save = sender.save

        @wraps(original_save)
        def new_save(self, *args, **kwargs):
            # Save editor in temporary variable, post_save will read this one
            self._history_editor = kwargs.pop('editor', getattr(self, '_history_editor', None))
            original_save(self, *args, **kwargs)

        sender.save = new_save

    def create_set_editor_method(self, sender):
        """
        Add a set_editor method to the model which has a history.
        """
        if hasattr(sender, 'set_editor'):
            raise Exception('historicalrecords cannot add method set_editor to %s' % sender.__class__.__name__)

        def set_editor(self, editor):
            """
            Set the editor (User object) to be used in the historicalrecord during the next save() call.
            """
            self._history_editor = editor
        sender.set_editor = set_editor

    def create_history_model(self, model):
        """
        Creates a historical model to associate with the model provided.
        """
        rel_nm = '_%s_history' % model._meta.object_name.lower()
        rel_nm_user = '_%s_history_editor' % model._meta.object_name.lower()
        important_field_names = self.get_important_field_names(model)

        def get_verbose_name(model, field_name):
            for f in model._meta.fields:
                if f.name == field_name:
                    return f.verbose_name

        class HistoryEntryMeta(ModelBase):
            """
            Meta class for history model. This will rename the history model,
            and copy the necessary fields from the other model.
            """
            def __new__(c, name, bases, attrs):
                # Rename class
                name = 'Historical%s' % model._meta.object_name

                # This attribute is required for a model to function properly.
                attrs['__module__'] = self._module or model.__module__

                # Copy attributes from base class
                attrs.update(self.copy_fields(model))
                attrs.update(Meta=type('Meta', (), self.get_meta_options(model)))

                return ModelBase.__new__(c, name, bases, attrs)

        class HistoryEntry(models.Model):
            """
            History entry
            """
            __metaclass__ = HistoryEntryMeta

            history_id = models.AutoField(primary_key=True)
            history_date = models.DateTimeField(default=timezone.now)
            history_type = models.CharField(max_length=1, choices=(
                    ('+', 'Created'),
                    ('~', 'Changed'),
                    ('-', 'Deleted'),
                ))
            history_object = HistoricalObjectDescriptor(model, self.get_important_field_names(model))
            history_editor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                                               related_name=rel_nm_user)

            def __unicode__(self):
                return u'%s as of %s' % (self.history_object, self.history_date)

            @property
            def previous_entry(self):
                try:
                    return self.history_object.history.order_by('-history_id').filter(history_id__lt=self.history_id)[0]
                except IndexError:
                    return None

            @property
            def modified_fields(self):
                """
                Return a list of which field have been changed during this save.
                """
                previous_entry = self.previous_entry
                if previous_entry:
                    modified = []
                    for field in important_field_names:
                        from_value = getattr(previous_entry, field)
                        to_value = getattr(self, field)
                        if from_value != to_value:
                            modified.append(HistoryChange(field, from_value, to_value, get_verbose_name(model, field)))
                    return modified
                else:
                    # No previous history entry, so actually everything has been modified.
                    return [ HistoryChange(f, None, getattr(self, f), get_verbose_name(model, f)) for f in important_field_names ]

        return HistoryEntry

    def get_important_fields(self, model):
        """ Return the list of fields that we care about.  """
        for f in model._meta.fields:
            if f.name == 'id' or not self._fields or f.name in self._fields:
                yield f

    def get_important_field_names(self, model):
        """ Return the names of the fields that we care about.  """
        return [ f.attname for f in self.get_important_fields(model) ]

    def copy_fields(self, model):
        """
        Creates copies of the model's original fields, returning
        a dictionary mapping field name to copied field object.
        """
        fields = { }
        for field in self.get_important_fields(model):
            field = copy.deepcopy(field)
            field_name = field.name

            if isinstance(field, models.AutoField):
                # The historical model gets its own AutoField, so any
                # existing one must be replaced with an IntegerField.
                field.__class__ = models.IntegerField

            if field.primary_key or field.unique:
                # Unique fields can no longer be guaranteed unique,
                # but they should still be indexed for faster lookups.
                field.primary_key = False
                field._unique = False
                field.db_index = True

            if isinstance(field, models.ForeignKey):
                # Do not use a related name for foreign keys, or it will clash with the
                # original model. This uses the private representation of ForiegnKey and
                # may not be compatible with future Django versions.
                field.rel.related_name = '+'

            if isinstance(field, models.OneToOneField):
                # OneToOne relations in the model should be converted to
                # ForeignKeys as it is now possible that it is no longer
                # unique.
                field = models.ForeignKey(to=field.rel.to, on_delete=models.CASCADE, related_name="+", null=True,
                                          blank=True)

            fields[field_name] = field

        return fields

    def get_meta_options(self, model):
        """
        Returns a dictionary of fields that will be added to
        the Meta inner class of the historical record model.
        """
        return {
            'ordering': ('-history_id',),
            'get_latest_by': 'history_id'
        }

    def post_save(self, instance, created, **kwargs):
        """
        During post-save, create historical record if none has been created before,
        or when the saved instance has fields which differ from the most recent
        historicalrecord.
        """
        # if the 'raw' keyword argument was added and is True, we are loading raw data
        # (for example from a fixture) and we don't want to execute this hook.
        if 'raw' in kwargs and kwargs['raw']:
            return
        # Decide whether to save a history copy: only when certain fields were changed.
        save = True
        try:
            most_recent = instance.history.most_recent()
            save = False
            for field in self.get_important_field_names(instance):
                if getattr(instance, field) != getattr(most_recent, field):
                    save = True
        except instance.DoesNotExist, e:
            pass

        # Create historical record
        if save:
            self.create_historical_record(instance, instance._history_editor, created and '+' or '~')

    def post_delete(self, instance, **kwargs):
        self.create_historical_record(instance, None, '-')

    def create_historical_record(self, instance, editor, type):
        manager = getattr(instance, self.manager_name)
        attrs = {}
        for field in self.get_important_field_names(instance):
            attrs[field] = getattr(instance, field)
        manager.create(history_type=type, history_editor=editor, **attrs)

class HistoricalObjectDescriptor(object):
    def __init__(self, model, important_fields):
        self.model = model
        self.important_fields = important_fields

    def __get__(self, instance, owner):
        values = dict( (f, getattr(instance, f)) for f in self.important_fields)
        return self.model(**values)
