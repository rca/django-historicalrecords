from history.models import HistoricalRecords


"""
# Usage example:

from history.utils import monkeypatch_history_for_model
monkeypatch_history_for_model(User, 'history', Profile.__module__)
                # Register in the same app as our Profile model
"""


def monkeypatch_history_for_model(model, attribute_name, module):
    """
    Add a history field to this model, callable through attribute_name.
    module should be a models.py file of a django application where this
    model is registered. (Important for South migrations.)
    """
    history = HistoricalRecords(module=module)
    history.contribute_to_class(model, attribute_name)
    history.finalize(model)

