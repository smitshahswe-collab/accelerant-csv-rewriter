"""Reproducible timing for CSVRewriter on a synthetic file.

    python benchmark.py            # 1,000,000 rows x 12 columns
    python benchmark.py 200000     # custom row count
"""
import os
import resource
import sys
import tempfile
import time

from main import CARS, CSVRewriter

ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
HEADER = ',,,,,,,,,,,\r\nrow,aligator,BearCat,Domestic Cat,Dog, Elephant,,grafe,Horse,"lizard,\\"Iguana\\"",,Kangaroo\r\n'


def timed(label, fn):
    start = time.perf_counter()
    result = fn()
    print(f'{label:<28}{time.perf_counter() - start:>8.2f} s')
    return result


with tempfile.TemporaryDirectory() as tmp:
    src, dst = os.path.join(tmp, 'big.csv'), os.path.join(tmp, 'out.csv')
    with open(src, 'w', newline='') as f:
        f.write(HEADER)
        f.writelines(f'{i},1,1,1,1,1,1,1,1,1,1,{i}\r\n' for i in range(1, ROWS + 1))
    print(f'{ROWS:,} rows x 12 columns, {os.path.getsize(src) / 1e6:.0f} MB on disk\n')

    rw = timed('load (parse + header)', lambda: CSVRewriter(src, CARS, header_escapechar='\\'))
    timed('match_columns', rw.match_columns)
    timed('rename_columns', rw.rename_columns)
    timed('drop_rows([3, 7])', lambda: rw.drop_rows([3, 7]))
    timed('to_csv', lambda: rw.to_csv(dst))
    # ru_maxrss is in bytes on macOS but kilobytes on Linux
    divisor = 1024 * 1024 if sys.platform == 'darwin' else 1024
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / divisor
    print(f'\npeak memory: {peak:.0f} MB')
