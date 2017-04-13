Recipe Analyzer and Multi-package Build Optimizer

Performs a few useful functions upon a collection of Conda build recipes:

- Sort a collection of recipes based on peer build dependencies (recipes that are
  part of the provided collection) such that packages may be built incrementally in isolation
  without conda pulling in additional dependencies during the builds. Output the ordered list
  to the terminal or a file.
- Cull the collection of sorted recipes by removing ones that would generate package file names
  already existing in a given distribution channel.

`rambo.py -h` for usage information
