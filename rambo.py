#!/usr/bin/env python3

'''
RAMBO - Recipe Analyzer and Multi-package Build Optimizer

Requires conda to be installed on the path in order to access the recipe
renderer 'conda_build.cli.main_render'.

 TODO: Correct conda recipe renderer RuntimeError "'numpy x.x' requires
       external setting" when parsing meta file for astroconda-dev/astropy
'''

import os
import sys
from yaml import safe_load
from io import StringIO
from copy import deepcopy
import argparse

# Class provided by conda-build for performing string interpolation of
# jinja2-enhanced <recipe>/meta.yaml files to produce legal YAML.
import conda_build.cli.main_render as cbr


class meta(object):
    '''Holds metadata for a recipe obtained from the recipe's meta.yaml file,
    certain values derived from that data, and methods to calculate those
    derived values.'''

    def __init__(self, recipe_dir):
        self.yaml = None
        self.recipe_dirname = os.path.basename(recipe_dir)
        self.valid = False
        self.complete = False
        self.name = None

        self.num_bdeps = 0
        self.deps = []

        self.peer_bdeps = []

        self.import_metadata(recipe_dir)
        self.derive_values()

        # self.unite_deps() # Test if needed.

    def import_metadata(self, rdir):
        '''Read in the package metadata from the given file then pass it
        through the conda recipe renderer to perform string interpolation and
        produce legal YAML text which is then parsed and stored.'''
        if os.path.isfile(rdir + '/meta.yaml'):
            # Redirect stdout for each call to cbr.execute since it only
            # writes to stdout.
            capture = StringIO()
            save_stdout = sys.stdout
            sys.stdout = capture
            cbr.execute([rdir])
            # Restore stdout.
            sys.stdout = save_stdout
            yaml = safe_load(capture.getvalue())
            self.yaml = yaml
            self.valid = self.is_valid()
            self.complete = self.is_complete()
            if self.valid:
                self.name = self.yaml['package']['name']
        else:
            print('Recipe directory {0} has no meta.yaml file.'.format(
                self.recipe_dirname))

    def derive_values(self):
        if self.complete:
            self.num_bdeps = len(self.yaml['requirements']['build'])
            for req in self.yaml['requirements']['build']:
                self.deps.append(req.split()[0])

    def unite_deps(self):
        '''Store the union of the simple names (no version specifications) of
        build and run dependencies in .deps.'''
        if self.complete:
            for key in ['build', 'run']:
                for req in self.yaml['requirements'][key]:
                    self.deps.append(req.split()[0])
            self.deps = set(self.deps)

    def deplist(self, deptype):
        '''Return the simplified (no version info, if present) list of
        dependency names of the given type.'''
        lst = []
        for dep in self.yaml['requirements'][deptype]:
            lst.append(dep.split()[0])
        return lst

    def is_valid(self):
        '''Does the metadata for this recipe contain the minimum information
        necessary to process?'''
        valid = True
        if 'name' not in self.yaml.get('package', {}):
            complete = False
        return valid

    def is_complete(self):
        '''Is the metadata for this recipe complete enough to allow for use
        in build-order optimization?'''
        complete = True
        if 'build' not in self.yaml.get('requirements', {}):
            complete = False
        return complete


class metaSet(object):
    '''A collection of mulitple recipe metadata objects from a directory
    specification, and methods for manipulationg and querying this
    collection.'''

    ignore_dirs = ['.git', 'template']

    def __init__(self, directory):
        self.metas = []
        self.incomplete_metas = []
        self.names = []
        self.read_recipes(directory)
        self.derive_values()
        self.sort_by_peer_bdeps()
        self.merge_metas()

    def read_recipes(self, directory):
        '''Process a directory reading in each conda recipe found, creating
        a list of metadata objects for use in analyzing the collection of
        recipes as a whole.'''
        recipe_dirnames = os.listdir(directory)
        for rdirname in recipe_dirnames:
            if rdirname in self.ignore_dirs:
                continue
            rdir = directory + '/' + rdirname
            m = meta(rdir)
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
        latter condition likely means there is a circular dependency that
        needs to be manually resolved.'''
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

    def print_by_tier(self):
        print('                              num  num      peer')
        print('         name               bdeps  peer     bdep     pos.')
        print('                                   bdeps    indices  OK?')
        print('----------------------------------------------------------')
        num_notOK = 0
        for num_peer_bdeps in range(0, 16):
            for idx, m in enumerate(self.metas):
                if (len(m.peer_bdeps) == num_peer_bdeps):
                    if not self.position_OK(m.name):
                        num_notOK = num_notOK + 1
                    print('{0:>28}  {1:{wid}}  {2:{wid}}  idx={3:{wid}}'
                          ' {4} {5}'.format(
                                m.name,
                                m.num_bdeps,
                                len(m.peer_bdeps),
                                idx,
                                self.peer_bdep_indices(m.name),
                                self.position_OK(m.name),
                                wid=2))
            print()
        print('Num not in order = {0}/{1}'.format(num_notOK, len(self.metas)))

    def print_details(self):
        num_notOK = 0
        print('                              num  num      peer')
        print('         name               bdeps  peer     bdep     pos.')
        print('                                   bdeps    indices  OK?')
        print('----------------------------------------------------------')
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
                          wid=2))
        print('Num not in order = {0}/{1}'.format(num_notOK, len(self.metas)))

    def print(self):
        '''Prints the list of package names in the order they appear in
        self.metas to stdout, suitable for ingestion by other tools
        during a build process.'''
        for m in self.metas:
            print('{0}'.format(m.name))

# ----


def print_ordered(mset):
    '''Perform a multi-pass build order optimization on the package metadata
    and print a simple ordered list of package names to stdout, suitable
    for piping to other programs.'''
    mset.multipass_optimize()
    mset.print()


def print_details(mset):
    '''Perform a multi-pass build order optimization on the package metadata
    and print a detailed summary of each package's dependency totals, index,
    dependency indices, and build position status.'''
    mset.multipass_optimize()
    mset.print_details()


def main(argv):

    parser = argparse.ArgumentParser()
    parser.add_argument('--ordered', action='store_true')
    parser.add_argument('--details', action='store_true')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('recipes_dir', type=str)
    args = parser.parse_args()
    recipes_dir = os.path.normpath(args.recipes_dir)

    mset = metaSet(recipes_dir)

    if args.ordered:
        print_ordered(mset)

    if args.details:
        print_details(mset)


if __name__ == "__main__":
    main(sys.argv)
