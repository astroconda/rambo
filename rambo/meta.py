#!/usr/bin/env python

'''
Requires conda & conda-build to be installed in a path that appears in the
python interprer's search list in order to access the API machinery via
'conda_build.api.
'''

from __future__ import print_function
import os
import sys
import time
import re
from copy import deepcopy
import argparse
import subprocess
from six.moves import urllib
import codecs
import yaml
from yaml import safe_load
import json
from jinja2 import Environment, FileSystemLoader, Template
from ._version import __version__
try:
    import conda_build.api
    from conda_build.api import Config
except ImportError:
    print('conda-build must be installed in order to use this tool. \n'
          'Either conda-build is not installed, or you are working in an \n'
          'activated conda environment. \n'
          'If conda-build is installed deactivate the environment currently \n'
          'enabled or explicitly switch to the conda "root" environment to \n'
          'allow use of conda-build.')

DEFAULT_MINIMUM_NUMPY_VERSION = '1.11'
CONDA_BUILD_MAJOR_VERSION = conda_build.__version__[0]

class Meta(object):
    '''Holds metadata for a recipe obtained from the recipe's meta.yaml file,
    certain values derived from that data, and methods to calculate those
    derived values.'''

    def __init__(self, recipe_dir, versions, channel, dirty=False):
        self.recipe_dirname = os.path.basename(recipe_dir)
        self.versions = versions
        self.channel = channel
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
        self.import_metadata(recipe_dir, True)
        self.derive_values()
        self.canonical_name = ''
        # Whether or not the package with this metadata
        # already exists in the channel archive
        self.archived = False
        self.render_canonical()

    def import_metadata(self, rdir, skip_render_of_archived=True):
        '''Read in the package metadata from the given recipe directory via
        the conda recipe renderer to perform string interpolation and
        store the values in a dictionary.'''
        if os.path.isfile(rdir + '/meta.yaml'):
            print('========================================'
                  '========================================')
            print('Rendering recipe for {}'.format(rdir))

            if CONDA_BUILD_MAJOR_VERSION == '2':
                self.render_payload = conda_build.api.render(
                    rdir,
                    dirty=self.dirty,
                    python=self.versions['python'],
                    Numpy=self.versions['numpy'])
                # conda-build v2.x render() returns a tuple:
                #  (MetaData, bool, bool)
                self.metaobj = self.render_payload[0]

            if CONDA_BUILD_MAJOR_VERSION == '3':
                self.render_payload = conda_build.api.render(
                    rdir,
                    dirty=self.dirty,
                    python=self.versions['python'],
                    numpy=self.versions['numpy'],
                    channel_urls=[self.channel],
                    filename_hashing=False)  # enables --old-build-string
                # conda-build v3.x render() returns a list of tuples:
                #  [(MetaData, bool, bool)]
                self.metaobj = self.render_payload[0][0]

            self.mdata = self.metaobj.meta
            self.valid = self.is_valid()
            self.complete = self.is_complete()
            if self.valid:
                self.name = self.mdata['package']['name']
            if self.metaobj.skip():
                print('skipping on selected platform due to directive'
                      ': {}'.format(self.name))
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

    def render_canonical(self):
        '''Generate the package's canonical name by using conda
        machinery to render the recipe.'''
        if CONDA_BUILD_MAJOR_VERSION == '2':
            output_file_path = conda_build.api.get_output_file_path(
                        self.metaobj,
                        python=self.versions['python'],
                        numpy=self.versions['numpy'],
                        dirty=self.dirty)
        if CONDA_BUILD_MAJOR_VERSION == '3':
            output_file_path = conda_build.api.get_output_file_paths(
                        self.metaobj,
                        python=self.versions['python'],
                        numpy=self.versions['numpy'],
                        dirty=self.dirty)[0]
        self.canonical_name = os.path.basename(output_file_path)
        print('Package canonical name: {}\n\n'.format(
                self.canonical_name))


class MetaSet(object):
    '''A collection of mulitple recipe metadata objects from a directory
    specification, and methods for manipulationg and querying this
    collection.'''

    ignore_dirs = ['.git', 'template']

    def __init__(self,
                 directory,
                 platform_arch,
                 versions,
                 culled,
                 manfile=None,
                 filter_nonpy=False,
                 dirty=False):
        '''Parameters:
        directory - a relative or absolute directory in which Conda
          recipe subdirectories may be found.
        versions - Dictionary containing python, numpy, etc, version
          information.'''
        self.metas = []
        self.platform_arch = platform_arch
        Config.platform = self.platform_arch.split('-')[0]
        self.versions = versions
        self.manfile = manfile
        self.manifest = None
        self.channel = None
        if self.manfile:
            self.read_manifest()
        if self.channel:
            self.channel_data = self.get_channel_data()
        self.filter_nonpy = filter_nonpy
        self.dirty = dirty
        self.culled = culled
        self.incomplete_metas = []
        self.names = []
        self.read_recipes(directory)
        if self.manfile:
            self.filter_by_manifest()
        self.derive_values()
        self.sort_by_peer_bdeps()
        self.merge_metas()
        if self.channel:
            self.flag_archived()

    def render_template_from_source(self, rdir):
        '''Render the recipe template using information harvested from
        the source tree obtained via git.'''

        directory, rdirname = os.path.split(rdir)
        env = Environment(loader=FileSystemLoader(directory))
        env.globals['environ'] = os.environ
        # Compute the build ID value and create a directory for use when
        # cloning the source to populate -dev recipe fields that rely upon
        # values supplied by git.
        # This approach was taken directly from conda-build.
        template = env.get_template(rdirname+'/meta.yaml')
        output = template.render(environment=env)

        # BaseLoader here is required to interpret all values as strings
        # to correctly handle things like version = '3.410` without
        # dropping the trailing 0.
        fastyaml = yaml.load(output, Loader=yaml.BaseLoader)
        pkgname = fastyaml['package']['name']
        build_id = pkgname + "_" + str(int(time.time() * 1000))

        # Locate the conda-build directory of the available conda installation
        conda_path = subprocess.check_output(['which', 'conda']).strip().decode()
        conda_root = conda_path.rstrip('/bin/conda')
        build_root = os.path.join(conda_root, 'conda-bld')
        build_dir = os.path.join(build_root, build_id, 'work')
        os.makedirs(build_dir)
        try:
            cmd = ['git', 'clone', fastyaml['source']['git_url'], build_dir]
            # clone repo into build_dir
            subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            # Check out specific git_rev, if provided in recipe.
            cdir = os.getcwd()
            os.chdir(build_dir)
            try:
                cmd = ['git', 'checkout', fastyaml['source']['git_rev']]
                subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            except(KeyError):
                pass
            os.chdir(cdir)
            script_dir = os.getcwd()
            os.chdir(build_dir)
            cmd = ['git', 'describe', '--tags', '--long']
            describe_output = subprocess.check_output(cmd).decode().split('-')
            os.chdir(script_dir)
            gd_tag = describe_output[0]
            gd_number = describe_output[1]
            gd_hash = describe_output[2]
            os.environ['GIT_DESCRIBE_TAG'] = gd_tag
            # Render the template using the obtained git describe variables.
            output = template.render(environment=env,
                    GIT_DESCRIBE_TAG=gd_tag,
                    GIT_DESCRIBE_NUMBER=gd_number,
                    GIT_DESCRIBE_HASH=gd_hash)
            return safe_load(output)
        except(KeyError):
            print('no source field in recipe: {}'.format(pkgname))

    def read_recipe_selection(self, directory, recipe_list):
        '''Process a directory, reading in each conda recipe found, creating
        a list of metadata objects for use in analyzing the collection of
        recipes as a whole.'''
        for rdirname in recipe_list:
            if rdirname in self.ignore_dirs:
                continue
            rdir = directory + '/' + rdirname

            # Default beavior is to quickly generate each package canonical name,
            # check for the presence of that name in the channel archive, and
            # skip rendering the recipe entirely if a package with that name
            # already exists. This saves great deal of time compared to rendering
            # every recipe to determine the canonical names.
            if self.culled:
                # If requested, quickly pre-process templates here, and only
                # instantiate (and render) metadata for recipes that have names
                # which do not appear in channel archive.
                env = Environment(loader=FileSystemLoader(directory))
                env.globals['environ'] = os.environ

                # First pass (for -dev), only pass for -contrib
                template = env.get_template(rdirname+'/meta.yaml')
                output = template.render(environment=env)
                # BaseLoader here is required to interpret all values as strings
                # to correctly handle things like version = '3.410` without
                # dropping the trailing 0.
                fastyaml = yaml.load(output, Loader=yaml.BaseLoader)
                # Determine if a 'pyXX' build string is necessary in the package name
                # by looking for 'python' in the run requirements.
                # TODO: Check for build requirement too?
                build_string = ''
                rundep_names = []
                blddep_names = []
                try:
                    rundep_names = [x.split()[0] for x in
                            fastyaml['requirements']['run']]
                except:
                    rundep_names = ''
                    # TODO INFO 
                    print('"Incomplete" metadata. No run requirements.')

                try:
                     blddep_names = [x.split()[0] for x in
                             fastyaml['requirements']['build']]
                except:
                    blddep_names = ''
                    print('"Incomplete" metadata. No build requirements.')

                # If filter-nonpy specified, skip all recipes that have a python
                # dependency.
                if self.filter_nonpy:
                    if 'python' not in rundep_names and 'python' not in blddep_names:
                        print('Skipping {} due to --filter-nonpy'.format(rdir))
                        continue

                if 'python' in rundep_names:
                    build_string = 'py{}_'.format(
                            self.versions['python'].replace('.',''))

                pkgname = fastyaml['package']['name']

                # Some recipes (mostly -dev) require extra steps to obtain the source for
                # generating template replacement values. Handle this extra processing
                # for all recipes that produced a 'dev' within the package name above.
                if re.search('\.dev', str(fastyaml['package']['version'])): 
                    fastyaml = self.render_template_from_source(rdir)

                fast_canonical = '{}-{}-{}{}.tar.bz2'.format(
                    pkgname,
                    str(fastyaml['package']['version']),
                    build_string,
                    fastyaml['build']['number'])

                # Move on to the next recipe dir if the package name
                # already exists in the channel data.
                if self.is_archived(fast_canonical):
                    print('fast_canonical: {}'.format(fast_canonical))
                    continue

            m = Meta(rdir,
                     versions=self.versions,
                     channel=self.channel,
                     dirty=self.dirty)
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
            rset = set.intersection(
                set(recipe_dirnames),
                set(self.manifest['packages']))
        else:
            rset = recipe_dirnames
        recipe_list = list(rset)
        recipe_list.sort()
        self.read_recipe_selection(directory, recipe_list)

    def read_manifest(self):
        mf = open(self.manfile, 'r')
        self.manifest = safe_load(mf)
        self.channel = self.manifest['channel_URL'].strip('/')
        self.channel += '/' + self.platform_arch

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

    def filter_nonpy(self):
        '''Deactivate all metadata objects that do not depend on python.
        Used when employing manifests that build non-python packages across
        multiple platforms. These packages only need to be built on a single
        platform. This method allows for these packages to be selectively
        removed from the build list.'''
        print('Deactivating all metadata objects that depend upon python.')

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

    def is_archived(self, canonical_name):
        if canonical_name in self.channel_data['packages'].keys():
            return True
        else:
            return False

    def flag_archived(self):
        '''Flag each meta as either being archived or not by comparing the
        locally generated package canonical name with the names present in
        the supplied channel archive data.
        Each meta's 'archived' attribute is set to True if found
        and False if not.'''
        for meta in self.metas:
            if meta.canonical_name in self.channel_data['packages'].keys():
                meta.archived = True

    def print_details(self):
        num_notOK = 0
        print('conda-build version     : ', conda_build.__version__)
        print('Python version specified: ', self.versions['python'])
        print('Numpy  version specified: ', self.versions['numpy'])
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
        print('Num not in order = {0}/{1}\n'.format(
            num_notOK,
            len(self.metas)))

    def print(self):
        '''Prints the list of package names in the order in which they appear
        in self.metas to stdout, suitable for ingestion by other tools during
        a build process.'''
        for m in self.metas:
            print('{0}'.format(m.name))

    def write(self, filename):
        '''Writes the list of package names in the order in which they appear
        in self.metas to stdout, suitable for ingestion by other tools during
        a build process.'''
        with open(filename,'w') as fh:
            for m in self.metas:
                fh.write('{0}\n'.format(m.name))

    def print_culled(self):
        '''Prints the list of package names for which the canonical name does
        not exist in the specified archive channel. List is presented in the
        order in which entries appear in self.metas.'''
        for m in [m for m in self.metas if m.active and not m.archived]:
            print('{0}'.format(m.name))

    def print_canonical(self):
        '''Prints list of canonical package names.'''
        for meta in self.metas:
            print('{0:>50}'.format(meta.canonical_name))

    def print_status_in_channel(self):
        '''Prints list of canonical package names and whether or not each
        has already been built and archived in the specified channel.'''
        statstr = {True: '', False: 'Not in channel archive'}
        for meta in self.metas:
            print('{0:>50}   {1}'.format(
                meta.canonical_name,
                statstr[meta.archived]))
