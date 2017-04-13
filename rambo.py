#!/usr/bin/env python3

'''
RAMBO - Recipe Analyzer and Multi-package Build Optimizer

Requires conda to be installed on the PATH in order to access the API
machinery via 'conda_build.api.
'''

import os
import sys
from copy import deepcopy
import argparse
import urllib.request
import codecs
import json
import conda_build.api

class meta(object):
    '''Holds metadata for a recipe obtained from the recipe's meta.yaml file,
    certain values derived from that data, and methods to calculate those
    derived values.'''

    def __init__(self, recipe_dir, versions, dirty=False):
        self.recipe_dirname = os.path.basename(recipe_dir)
        self.versions = versions
        self.dirty = dirty
        self.metaobj = None    # renderdata[0] (MetaData)
        self.mdata = None      # renderdata[0].meta (dict)

        self.valid = False
        self.complete = False
        self.name = None

        self.num_bdeps = 0
        self.deps = []

        self.peer_bdeps = []

        self.import_metadata(recipe_dir)
        self.derive_values()

        self.canonical_name = ''
        self.archived = False # Whether or not the package with this metadata
                              # already exists in the channel archive

        self.gen_canonical()

        # self.unite_deps() # Test if needed.

    def import_metadata(self, rdir):
        '''Read in the package metadata from the given recipe directory via
        the conda recipe renderer to perform string interpolation and
        store the values in a dictionary.'''
        if os.path.isfile(rdir + '/meta.yaml'):
            #print('      >>>>>>>> Importing metadata from {0}...'.format(self.recipe_dirname))
            # render() returns a tuple: (MetaData, bool, bool)
            self.metaobj = conda_build.api.render(rdir,
                            self.dirty,
                            python=self.versions['python'],
                            numpy=self.versions['numpy'])[0]
            self.mdata = self.metaobj.meta
            self.valid = self.is_valid()
            self.complete = self.is_complete()
            if self.valid:
                self.name = self.mdata['package']['name']
        else:
            print('Recipe directory {0} has no meta.yaml file.'.format(
                self.recipe_dirname))

    def derive_values(self):
        if self.complete:
            self.num_bdeps = len(self.mdata['requirements']['build'])
            for req in self.mdata['requirements']['build']:
                self.deps.append(req.split()[0])

    def unite_deps(self):
        '''Store the union of the simple names (no version specifications) of
        build and run dependencies in .deps.'''
        if self.complete:
            for key in ['build', 'run']:
                for req in self.mdata['requirements'][key]:
                    self.deps.append(req.split()[0])
            self.deps = set(self.deps)

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


class metaSet(object):
    '''A collection of mulitple recipe metadata objects from a directory
    specification, and methods for manipulationg and querying this
    collection.'''

    ignore_dirs = ['.git', 'template']

    def __init__(self,
                 directory,
                 versions,
                 channel,
                 dirty=False):
        '''Parameters:
        directory - a relative or absolute directory in which Conda
          recipe subdirectories may be found.
        versions - Dictionary containing python, numpy, etc, version
          information.'''
        self.versions = versions
        self.dirty = dirty
        self.metas = []
        self.incomplete_metas = []
        self.names = []
        self.read_recipes(directory)
        self.derive_values()
        self.sort_by_peer_bdeps()
        self.merge_metas()
        self.channel = channel
        if channel:
            self.channel_URL = channel.strip('/')
            self.channel_data = self.get_channel_data()
            self.flag_archived()

    def read_recipes(self, directory):
        '''Process a directory reading in each conda recipe found, creating
        a list of metadata objects for use in analyzing the collection of
        recipes as a whole.'''
        recipe_dirnames = os.listdir(directory)
        for rdirname in recipe_dirnames:
            if rdirname in self.ignore_dirs:
                continue
            rdir = directory + '/' + rdirname
            m = meta(rdir, versions=self.versions, dirty=self.dirty)
            if m.complete:
                self.metas.append(m)
                self.names.append(m.name)
            else:
                self.incomplete_metas.append(m)

    def merge_metas(self):
        '''Prepend the list of metas that do not have complete build
        dependency information to the main list.
        Also, add those names to the names list.'''
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
        approximation to a correct build order of all peers.'''
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
        jsonbytes = urllib.request.urlopen(self.channel_URL + '/repodata.json')
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
        print('                              num  num      peer', file=fh)
        print('         name               bdeps  peer     bdep     pos.', file=fh)
        print('                                   bdeps    indices  OK?', file=fh)
        print('----------------------------------------------------------', file=fh)
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
        print('Num not in order = {0}/{1}\n'.format(num_notOK,
            len(self.metas)), file=fh)

    def print(self, fh=sys.stdout):
        '''Prints the list of package names in the order in which they appear
        in self.metas to stdout, suitable for ingestion by other tools during
        a build process.'''
        for m in self.metas:
            print('{0}'.format(m.name), file=fh)

    def print_culled(self, fh=sys.stdout):
        '''Prints the list of package names for which the canonical name does not
        exist in the specified archive channel. List is presented in the order in
        which entries appear in self.metas.'''
        for m in self.metas:
            if not m.archived:
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
            print('{0:>50}   {1}'.format(meta.canonical_name,
                statstr[meta.archived]), file=fh)


# ----


def main(argv):

    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--culled', action='store_true',
            help='Print the ordered list of package names reduced to the set'
            ' of packages that do not already exist in the specified channel.'
            ' Requires --channel')
    parser.add_argument('-d', '--details', action='store_true',
            help='Display details used in determining build order and/or '
            'package culling.')
    parser.add_argument('-f', '--file',
            help='Send package list output to this file instead of stdout.')
    parser.add_argument('--channel', type=str,
            help='URL of conda channel repository to search for package list '
            'culling purposes.')
    parser.add_argument('--python', type=str,
            help='Python version to pass to conda machinery when rendering '
            'recipes. "#.#" format.')
    parser.add_argument('--numpy', type=str,
            help='Numpy version to pass to conda machinery when rendering '
            'recipes. "#.#" format.')
    parser.add_argument('--dirty', type=str,
            help='Use the most recent pre-existing conda work directory for '
            'each recipe instead of creating a new one. If a work directory '
            'does not already exist, the recipe is processed in the normal '
            'fashion.')
    parser.add_argument('recipes_dir', type=str)
    args = parser.parse_args()

    recipes_dir = os.path.normpath(args.recipes_dir)

    fh = None
    if args.file:
        fh = open(args.file, 'w')

    versions = {'python':'',
                'numpy':''}
    if args.python:
        versions['python'] = args.python
    if args.numpy:
        versions['numpy'] = args.numpy

    mset = metaSet(
            recipes_dir,
            versions=versions,
            channel=args.channel,
            dirty=args.dirty)

    mset.multipass_optimize()

    if args.details:
        mset.print_details(fh)
        if mset.channel:
            mset.print_status_in_channel(fh)
    elif args.culled:
        mset.print_culled(fh)
    else:
        mset.print(fh)

if __name__ == "__main__":
    main(sys.argv)
