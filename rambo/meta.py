#!/usr/bin/env python

'''
Requires conda & conda-build to be installed in a path that appears in the
python interprer's search list in order to access the API machinery via
'conda_build.api.
'''

from __future__ import print_function
import os
import sys
from copy import deepcopy
import argparse
from six.moves import urllib
import codecs
from yaml import safe_load
import json
from ._version import __version__
try:
    import conda_build.api
    from conda_build.config import Config
except ImportError:
    print('conda-build must be installed order to use this tool. \n'
          'Either conda-build is not installed, or you are working in an \n'
          'activated conda environment. \n'
          'If conda-build is installed deactivate the environment currently \n'
          'enabled or explicitly switch to the conda "root" environment to \n'
          'allow use of conda-build.')

DEFAULT_MINIMUM_NUMPY_VERSION = '1.11'


class Meta(object):
    '''Holds metadata for a recipe obtained from the recipe's meta.yaml file,
    certain values derived from that data, and methods to calculate those
    derived values.'''

    def __init__(self, recipe_dir, versions, dirty=False):
        self.recipe_dirname = os.path.basename(recipe_dir)
        self.versions = versions
        self.dirty = dirty
        self.metaobj = None     # renderdata[0] (MetaData)
        self.mdata = None       # renderdata[0].meta (dict)
        self.active = True  # Visit metadata in certain processing steps?
        self.valid = False
        self.complete = False
        self.name = None
        self.num_bdeps = 0
        self.deps = []
        self.peer_bdeps = []
        self.import_metadata(recipe_dir)
        self.derive_values()
        self.canonical_name = ''
        # Whether or not the package with this metadata
        # already exists in the channel archive
        self.archived = False
        self.gen_canonical()

    def import_metadata(self, rdir):
        '''Read in the package metadata from the given recipe directory via
        the conda recipe renderer to perform string interpolation and
        store the values in a dictionary.'''
        if os.path.isfile(rdir + '/meta.yaml'):
            # render() returns a tuple: (MetaData, bool, bool)
            self.metaobj = conda_build.api.render(
                rdir,
                dirty=self.dirty,
                python=self.versions['python'],
                numpy=self.versions['numpy'])[0]
            self.mdata = self.metaobj.meta
            self.valid = self.is_valid()
            self.complete = self.is_complete()
            if self.valid:
                self.name = self.mdata['package']['name']
            if self.metaobj.skip():
                print('skipping on selected platform due to directive: {}'.format(
                    self.name))
        else:
            print('Recipe directory {0} has no meta.yaml file.'.format(
                self.recipe_dirname))

    def derive_values(self):
        if self.complete:
            self.num_bdeps = len(self.mdata['requirements']['build'])
            for req in self.mdata['requirements']['build']:
                self.deps.append(req.split()[0])

    def deplist(self, deptype):
        '''Return the simplified (no version info, if present) list of
        dependency names of the given type.'''
        lst = []
        for dep in self.mdata['requirements'][deptype]:
            lst.append(dep.split()[0])
        return lst

    def is_valid(self):
        '''Does the metadata for this recipe contain the minimum information
        necessary to process?'''
        valid = True
        if 'package' not in self.mdata.keys():
            complete = False
        return valid

    def is_complete(self):
        '''Is the metadata for this recipe complete enough to allow for use
        in build-order optimization?'''
        complete = True
        if 'requirements' in self.mdata.keys():
            if 'build' not in self.mdata['requirements'].keys():
                complete = False
        else:
            complete = False
        return complete

    def gen_canonical(self):
        '''Generate the package's canonical name using available
        information.'''
        self.canonical_name = os.path.basename(
                conda_build.api.get_output_file_path(
                    self.metaobj,
                    python=self.versions['python'],
                    numpy=self.versions['numpy']))


class MetaSet(object):
    '''A collection of mulitple recipe metadata objects from a directory
    specification, and methods for manipulationg and querying this
    collection.'''

    ignore_dirs = ['.git', 'template']

    def __init__(self,
                 directory,
                 versions,
                 manfile=None,
                 dirty=False):
        '''Parameters:
        directory - a relative or absolute directory in which Conda
          recipe subdirectories may be found.
        versions - Dictionary containing python, numpy, etc, version
          information.'''
        self.metas = []
        self.platform = Config.platform
        self.versions = versions
        self.manfile = manfile
        self.manifest = None
        self.channel = None
        if self.manfile:
            self.read_manifest()
            self.filter_by_manifest()
        self.dirty = dirty
        self.incomplete_metas = []
        self.names = []
        self.read_recipes(directory)
        self.derive_values()
        self.sort_by_peer_bdeps()
        self.merge_metas()
        if self.channel:
            self.channel_data = self.get_channel_data()
            self.flag_archived()

    def read_recipe_selection(self, directory, recipe_list):
        '''Process a directory reading in each conda recipe found, creating
        a list of metadata objects for use in analyzing the collection of
        recipes as a whole.'''
        for rdirname in recipe_list:
            if rdirname in self.ignore_dirs:
                continue
            rdir = directory + '/' + rdirname
            m = Meta(rdir, versions=self.versions, dirty=self.dirty)
            if not m.metaobj.skip():
                if m.complete:
                    self.metas.append(m)
                    self.names.append(m.name)
                else:
                    self.incomplete_metas.append(m)

    def read_recipes(self, directory):
        recipe_dirnames = os.listdir(directory)
        # If a manifest was given, use it to filter the list of available
        # recipes.
        if self.manifest:
            recipe_list = set.intersection(
                set(recipe_dirnames),
                set(self.manifest['packages']))
        else:
            recipe_list = recipe_dirnames
        self.read_recipe_selection(directory, recipe_list)

    def read_manifest(self):
        mf = open(self.manfile, 'r')
        self.manifest = safe_load(mf)
        self.channel = self.manifest['channel_URL'].strip('/')
        self.channel += '/' + self.platform
        self.versions['numpy'] = str(self.manifest['numpy_version'])

    def filter_by_manifest(self):
        '''Leave only the recipe metadata entries that appear in the
        provided manifest list active.'''
        for meta in self.metas:
            if meta.name not in self.manifest['packages']:
                meta.active = False

    def merge_metas(self):
        '''Prepend the list of metas that do not have complete build
        dependency information to the main list.
        Also, add those names to the names list.'''
        # Sort alphabetically by name
        self.incomplete_metas = sorted(
            self.incomplete_metas,
            key=lambda meta: meta.name)
        for m in self.incomplete_metas[::-1]:
            self.metas.insert(0, m)

    def derive_values(self):
        '''Produce values from the set of recipes taken as a whole.'''
        self.calc_peer_bdeps()

    def calc_peer_bdeps(self):
        '''Produce and store a names-only list of the build dependencies
        for each recipe found to this set of recipes that each recipe
        references.'''
        for meta in self.metas:
            for name in meta.deps:
                if name in self.names:
                    meta.peer_bdeps.append(name)

    def sort_by_peer_bdeps(self):
        '''Sort the list of metadata objects by the number of peer build
        dependencies each has, in ascending order. This gives a good first
        approximation to a correct build order of all peers.  Peform an
        extra step here to reduce stochasticity of the order of packages
        within a given tier that all share the same number of peer_bdeps.
        The order of those items apparently varies from run to run.'''
        # First sort by alphabetical on name to make the subsequent
        # sorting deterministic.
        self.metas = sorted(self.metas, key=lambda meta: meta.name)
        self.metas = sorted(self.metas, key=lambda meta: len(meta.peer_bdeps))

    def index(self, mname):
        '''Return the index of a metadata object with the name 'mname'.'''
        for i, meta in enumerate(self.metas):
            if (meta.name == mname):
                return i
        raise IndexError('Name [{0}] not found.'.format(mname))

    def peer_bdep_indices(self, mname):
        '''Returns a list of the indices in the meta list corresponding to
        all the peer build dependencies (bdeps) of the given package
        metadata.'''
        indices = []
        for i, meta in enumerate(self.metas):
            if (meta.name == mname):
                for dep in meta.peer_bdeps:
                    indices.append(self.index(dep))
        return indices

    def position_OK(self, mname):
        '''If a package has peer build dependencies that all occur before
        the package in the sorted list of package recipes, the package's
        position in the build order list is acceptable.'''
        for i in self.peer_bdep_indices(mname):
            if i > self.index(mname):
                return False
        return True

    def relocate(self, mname):
        '''Relocate a meta object in the meta set such that all its internal
        dependencies appear earlier in the list than it does.
        The algorithm:
        For a package that does not have position_OK=True, examine the
        internal dependency indices. If any index is greater than the
        package's index, relocate the package to the index in the list just
        after the largest such dependency index.
        1. Deepcopy object into temp variable
        2. Insert copy into list at new index
        3. remove the original item from list'''
        idx = self.index(mname)
        new_idx = max(self.peer_bdep_indices(mname)) + 1
        temp = deepcopy(self.metas[idx])
        self.metas.insert(new_idx, temp)
        del self.metas[idx]

    def optimize_build_order(self):
        '''Makes a single pass through the list of (complete) package metadata,
        relocating in the list any item which is not in the correct slot in
        the build order.'''
        for m in self.metas:
            if not self.position_OK(m.name):
                self.relocate(m.name)

    def multipass_optimize(self, max_passes=8):
        '''Makes multiple passes over the list of metadata, optimizing during
        each pass until either the entire list is ordered correctly for
        building, or the maximum number of allowed passes is reached. The
        latter condition suggests there is a circular dependency that needs
        to be manually resolved.'''
        opass = 0
        num_notOK = 1
        while (num_notOK > 0 and opass < max_passes):
            opass = opass + 1
            num_notOK = 0
            self.optimize_build_order()
            for m in self.metas:
                if not self.position_OK(m.name):
                    num_notOK = num_notOK + 1
        if (opass == max_passes):
            print('Pass {0} of {1} reached. Check for circular '
                  'dependencies.'.format(
                    opass,
                    max_passes))
            return False
        return True

    def get_channel_data(self):
        '''Download the channel metadata from all specified conda package
        channel URLs, parse the JSON data into a dictionary.'''
        jsonbytes = urllib.request.urlopen(self.channel + '/repodata.json')
        # urllib only returns 'bytes' objects, so convert to unicode.
        reader = codecs.getreader('utf-8')
        return json.load(reader(jsonbytes))

    def flag_archived(self):
        '''Flag each meta as either being archived or not by generating the
        package canonical name, fetching the provided conda channel
        archive data, and searching the archive data for the generated
        name. Each meta's 'archived' attribute is set to True if found
        and False if not.'''
        for meta in self.metas:
            if meta.canonical_name in self.channel_data['packages'].keys():
                meta.archived = True

    def print_details(self, fh=sys.stdout):
        num_notOK = 0
        print('conda-build version     : ', conda_build.__version__)
        print('Python version specified: ', self.versions['python'])
        print('Numpy  version specified: ', self.versions['numpy'])
        print('                              num  num      peer', file=fh)
        print('         name               bdeps  peer     bdep     pos.',
              file=fh)
        print('                                   bdeps    indices  OK?',
              file=fh)
        print('----------------------------------------------------------',
              file=fh)
        for idx, m in enumerate(self.metas):
            if not self.position_OK(m.name):
                num_notOK = num_notOK + 1
            print('{0:>28}  {1:{wid}}  {2:{wid}}  idx={3:{wid}} {4} {5}'
                  .format(m.name,
                          m.num_bdeps,
                          len(m.peer_bdeps),
                          idx,
                          self.peer_bdep_indices(m.name),
                          self.position_OK(m.name),
                          wid=2), file=fh)
        print('Num not in order = {0}/{1}\n'.format(
            num_notOK,
            len(self.metas)), file=fh)

    def print(self, fh=sys.stdout):
        '''Prints the list of package names in the order in which they appear
        in self.metas to stdout, suitable for ingestion by other tools during
        a build process.'''
        for m in self.metas:
            print('{0}'.format(m.name), file=fh)

    def print_culled(self, fh=sys.stdout):
        '''Prints the list of package names for which the canonical name does
        not exist in the specified archive channel. List is presented in the
        order in which entries appear in self.metas.'''
        for m in [m for m in self.metas if m.active and not m.archived]:
            print('{0}'.format(m.name), file=fh)

    def print_canonical(self, fh=sys.stdout):
        '''Prints list of canonical package names.'''
        for meta in self.metas:
            print('{0:>50}'.format(meta.canonical_name), file=fh)

    def print_status_in_channel(self, fh=sys.stdout):
        '''Prints list of canonical package names and whether or not each
        has already been built and archived in the specified channel.'''
        statstr = {True: '', False: 'Not in channel archive'}
        for meta in self.metas:
            print('{0:>50}   {1}'.format(
                meta.canonical_name,
                statstr[meta.archived]), file=fh)
