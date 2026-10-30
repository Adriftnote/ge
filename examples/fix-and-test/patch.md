You propose a fix. Do not edit files — return a patch as JSON only.

Target file: `stats.py` (path relative to your working folder).

## Why
`median([4, 1, 3, 2])` returns 3. The median of an even-length list must be the mean of the two middle values (2.5).

## Do
1. Fix `median` for even-length input. Odd-length behaviour must not change.

## Do not
- Touch `mean` or the tests.

## Format
`patches[].old_str` must appear exactly once in the file (whitespace included). `new_str` replaces it.
`evidence`: at least 2 items, each a claim plus a searchable line from the file.
