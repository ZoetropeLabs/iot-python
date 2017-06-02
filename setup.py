import sys
sys.path.insert(0, 'src')

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

setup(
    name='ibmiotf',
    version="0.2.8",
    author='David Parker',
    author_email='parkerda@uk.ibm.com',
    package_dir={'': 'src'},
    packages=['ibmiotf', 'ibmiotf.codecs'],
    package_data={'ibmiotf': ['*.pem']},
    url='https://github.com/ibm-watson-iot/iot-python',
    license=open('LICENSE').read(),
    description='Python Client for IBM Watson IoT Platform',
    long_description=open('README.rst').read(),
    install_requires=[
        "iso8601 >= 0.1.10",
        "pytz >= 2014.7",
        "paho-mqtt >= 1.2",
        "requests_toolbelt >= 0.7.0",
        "dicttoxml >= 1.7.4",
        "xmltodict >= 0.10.2"
    ],
    dependency_links = ["http://github.com/ZoetropeLabs/paho.mqtt.python/tarball/master#egg=paho-mqtt-2.0.0"],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Topic :: Communications',
        'Topic :: Internet',
        'Topic :: Software Development :: Libraries :: Python Modules'
    ]
)
