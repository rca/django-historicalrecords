from setuptools import setup, find_packages

def listify(filename):
    return filter(None, open(filename, 'r').read().split('\n'))

setup(
    name = "django-historicalrecords-rca",
    version = "1.2.4",
    url = 'http://github.com/rca/django-historicalrecords',
    license = 'BSD',
    description = "Marty Alchin's HistoricalRecords from the ProDjango book.",
    long_description = open('README.rst','r').read(),
    author = 'Marty Alchin',
    packages = find_packages('src'),
    package_dir = {'': 'src'},
    install_requires = listify('requirements.pip'),
    classifiers = listify('CLASSIFIERS.txt'),
)

