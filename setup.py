from setuptools import setup, find_packages

version = {}
with open("rambo/_version.py") as fp:
        exec(fp.read(), version)
        # use: version['__version__'] to access

setup(
    name='rambo',
    version=version['__version__'],
    author='Matt Rendina',
    author_email='mrendina@stsci.edu',
    description='Recipe Analyzer and Multi-package Build Optimizer',
    url='https://github.com/astroconda/rambo',
    license='GPLv2',
    classifiers=[
        'License :: OSI Approved :: BSD License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Natural Language :: English',
        'Topic :: Software Development :: Build Tools',
    ],
    packages=find_packages(),
    package_data={'': ['README.md', 'LICENSE.txt']},
    entry_points = {
        'console_scripts': ['rambo=rambo.__main__:main'],
    }
)
