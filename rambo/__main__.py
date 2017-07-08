#!/usr/bin/env python

'''
RAMBO - Recipe Analyzer and Multi-package Build Optimizer
'''

from __future__ import print_function
import os
import sys
import argparse
from . import meta


def main(argv=None):

    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(
            prog='rambo',
            description='Recipe Analyzer and Multi-Package Build Optimizer')
    parser.add_argument('-p', '--platform', type=str)
    parser.add_argument(
            '--python',
            type=str,
            help='Python version to pass to conda machinery when rendering '
            'recipes. "#.#" format. If not specified, the version of python'
            ' hosting conda_build.api is used.')
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
            ' in the supplied manifest file.')
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
    parser.add_argument('recipes_dir', type=str)
    args = parser.parse_args()

    recipes_dir = os.path.normpath(args.recipes_dir)

    fh = None
    if args.file:
        fh = open(args.file, 'w')

    versions = {'python': '', 'numpy': ''}
    if args.python:
        versions['python'] = args.python

    versions['numpy'] = meta.DEFAULT_MINIMUM_NUMPY_VERSION

    meta.Config.platform = args.platform

    mset = meta.MetaSet(
            recipes_dir,
            versions=versions,
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
