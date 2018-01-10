#!/usr/bin/env python

'''
RAMBO - Recipe Analyzer and Multi-package Build Optimizer
'''

from __future__ import print_function
import os
import sys
import argparse
from . import meta

def get_platform_arch():
    plat_alias = sys.platform
    if plat_alias == 'darwin':
        plat_alias = 'osx'
    is64bit = (sys.maxsize > 2**32)
    arch_bits = '64'
    if not is64bit:
        arch_bits = '32'
    platform_arch = '{}-{}'.format(plat_alias, arch_bits)
    return platform_arch


def main(argv=None):

    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(
            prog='rambo',
            description='Recipe Analyzer and Multi-Package Build Optimizer')
    parser.add_argument('-p',
            '--platform_arch',
            type=str,
            help='The platform-arch specification string in the format that'
            ' conda uses. I.e. "linux-64" or "osx-64". If not specified, the'
            ' platform of the host system is used.')
    parser.add_argument(
            '--python',
            type=str,
            help='Python version to pass to conda machinery when  '
            'recipes. "#.#" format. If not specified, the version of python'
            ' hosting conda_build.api is used.')
    parser.add_argument(
            '--numpy',
            type=str,
            help='numpy version to pass to conda machinery when rendering '
            'recipes. "#.#" format. If not specified, the version value \'{}\''
            ' is used.'.format(
                meta.DEFAULT_MINIMUM_NUMPY_VERSION))
    parser.add_argument(
            '-m',
            '--manifest',
            type=str,
            help='Use this file to filter the list of recipes to process.')
    parser.add_argument(
            '-f',
            '--file',
            type=str,
            help='Send package list output to this file instead of stdout.')
    parser.add_argument(
            '-c',
            '--culled',
            action='store_true',
            help='Print the ordered list of package names reduced to the set'
            ' of packages that do not already exist in the channel specified'
            ' in the supplied manifest file. This uses fast canonical name '
            'generation to skip rendering recipes that would produce a file'
            ' name already present in the channel index.\n'
            'NOTE: Not using this option will attempt to render every '
            ' recipe in the manifest and may take a long time for recipes '
            'that have long dependency chains and for those which rely upon'
            ' a git clone operation to obtain values needed to render the '
            'recipe.')
    parser.add_argument(
            '-d',
            '--details',
            action='store_true',
            help='Display details used in determining build order and/or '
            'package culling.')
    parser.add_argument(
            '--dirty',
            action='store_true',
            help='Use the most recent pre-existing conda work directory for '
            'each recipe instead of creating a new one. If a work directory '
            'does not already exist, the recipe is processed in the normal '
            'fashion. Used mostly for testing purposes.')
    parser.add_argument(
            '-v',
            '--version',
            action='version',
            version='%(prog)s ' + meta.__version__,
            help='Display version information.')
    parser.add_argument('recipes_dir', type=str, help='Required')
    args = parser.parse_args()

    recipes_dir = os.path.normpath(args.recipes_dir)

    fh = None
    if args.file:
        fh = open(args.file, 'w')

    versions = {'python': '', 'numpy': ''}
    if args.python:
        versions['python'] = args.python

    if args.numpy:
        versions['numpy'] = args.numpy
    else:
        versions['numpy'] = meta.DEFAULT_MINIMUM_NUMPY_VERSION

    if args.platform_arch:
        platform_arch = args.platform_arch
    else:
        platform_arch = get_platform_arch()

    mset = meta.MetaSet(
            recipes_dir,
            platform_arch,
            versions=versions,
            culled=args.culled,
            dirty=args.dirty,
            manfile=args.manifest)

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
    main()
