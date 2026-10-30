"""CSVRewriter: clean up messy CSV headers and unwanted rows.

Usage:
    python main.py                          # input.csv -> output.csv (drops rows 3 and 7 as an example)
    python main.py --drop 5 10              # choose the row numbers to drop
    python main.py --drop                   # drop nothing
    python main.py --dry-run                # show the column matches, write nothing
    python main.py --alias "BearCat=Bear"   # force a header-to-name mapping (repeatable)
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass

import pandas as pd
from fuzzywuzzy import fuzz

log = logging.getLogger('csvrewriter')

# List of valid column names
CARS = ['Alligator', 'Bear', 'Cat', 'Dog', 'Elephant', 'Frog', 'Giraffe',
        'Horse', 'Iguana', 'Jaguar', 'Kangaroo']

# Scores are 0-100 (fuzzywuzzy scale). Exact > alias > whole-word > fuzzy.
DEFAULT_THRESHOLD = 80
EXACT_SCORE = 100
ALIAS_SCORE = 99            # explicit alias; deliberately below an exact match (see _score)
TOKEN_MATCH_SCORE = 95      # a whole word of the header equals a valid name ("Domestic Cat" -> Cat)
REVIEW_BELOW = 90           # CLI flags matches under this score for human review
LEGACY_ESCAPE = '\\'


class CSVRewriterError(ValueError):
    """A problem with the input that the user can fix (bad file, bad option)."""


class HeaderFormatError(CSVRewriterError):
    """The header row could not be parsed as standard CSV."""


@dataclass(frozen=True)
class ColumnMatch:
    """Result of matching one source column."""
    position: int            # 0-based position in the file
    original: str            # parsed header text, including surrounding whitespace
    matched: str | None      # valid name, or None if nothing was close enough
    score: float             # heuristic similarity 0-100 (0 if unmatched)


def _fold(text: str) -> str:
    """Casefold and strip accents, so 'Café' and 'cafe' compare equal."""
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _normalize(text: str) -> str:
    """Keep only letters/digits (any script): ' Elephant ' -> 'elephant'."""
    return ''.join(c for c in _fold(text) if c.isalnum())


def _tokens(text: str) -> list[str]:
    # Split CamelCase before folding, so BearCat has two real tokens.
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    return [t for t in re.split(r'[\W_]+', _fold(text)) if t]


class CSVRewriter:
    def __init__(self, file_name: str, valid_columns: list[str],
                 threshold: int = DEFAULT_THRESHOLD, row_column: str = 'row',
                 *, header_escapechar: str | None = None,
                 aliases: dict[str, str] | None = None):
        """
        file_name      CSV to clean.
        valid_columns  known-good column names.
        threshold      minimum score (0-100) to accept a match.
        row_column     column holding each row's row number. If the file has no such
                       column, drop_rows falls back to 1-based position of data rows.
        header_escapechar  explicit legacy escape character, applied to the header only.
        aliases        explicit header -> valid-name mappings (case/space-insensitive).
                       They beat fuzzy scores but never an exact match.
        """
        if not valid_columns:
            raise CSVRewriterError('valid_columns must not be empty')
        if not 0 <= threshold <= 100:
            raise CSVRewriterError('threshold must be between 0 and 100')
        self.file_name = file_name
        self.valid_columns = list(valid_columns)
        self.threshold = threshold
        self.row_column = row_column
        self.aliases = self._build_aliases(aliases or {})
        self.df = self._load(file_name, header_escapechar)
        self._matches: list[ColumnMatch] | None = None

    def _build_aliases(self, aliases: dict[str, str]) -> dict[str, str]:
        valid_keys = {_normalize(v) for v in self.valid_columns}
        built: dict[str, str] = {}
        for header, target in aliases.items():
            key = _normalize(header)
            if target not in self.valid_columns:
                raise CSVRewriterError(f'alias target {target!r} is not a valid column name')
            if not key:
                raise CSVRewriterError('alias header must not be empty')
            if key in valid_keys:
                raise CSVRewriterError(f'alias {header!r} is itself a valid column name')
            if built.get(key, target) != target:
                raise CSVRewriterError(f'conflicting aliases for {header!r}')
            built[key] = target
        return built

    # ------------------------------------------------------------------ loading
    @staticmethod
    def _load(file_name: str, header_escapechar: str | None = None) -> pd.DataFrame:
        """Read standard CSV as text; legacy header escaping requires an explicit option.

        Data records always preserve literal backslashes, leading zeros, quotes and
        embedded newlines. Strict parsing rejects malformed quoting instead of
        silently absorbing records into a header. The first record containing any
        content is the header, so a blank opening record (",,,") is skipped.
        """
        reader = None
        parsing_header = True
        offset = 0                  # lines consumed by the header reader
        try:
            with open(file_name, newline='', encoding='utf-8-sig') as source:
                reader = csv.reader(source, escapechar=header_escapechar, strict=True)
                header = next((r for r in reader if any(c.strip() for c in r)), None)
                if header is None:
                    raise CSVRewriterError(f'{file_name} contains no data')
                parsing_header = False
                offset = reader.line_num
                reader = csv.reader(source, strict=True)      # body: no escape character
                body = []
                for record in reader:
                    if not record:                            # physically empty line
                        continue
                    if len(record) != len(header):
                        raise CSVRewriterError(
                            f'line {offset + reader.line_num}: expected {len(header)} fields, found {len(record)}')
                    body.append(record)
        except FileNotFoundError:
            raise CSVRewriterError(f'file not found: {file_name}') from None
        except UnicodeDecodeError:
            raise CSVRewriterError(f'{file_name} is not valid UTF-8 text') from None
        except csv.Error as exc:
            kind = HeaderFormatError if parsing_header else CSVRewriterError
            raise kind(f'{file_name}: malformed CSV near line {offset + reader.line_num}: {exc}') from None
        return pd.DataFrame(body, columns=header, dtype=str)

    # ----------------------------------------------------------------- matching
    def _score(self, header: str, valid: str) -> tuple[float, int]:
        """Return (score, plain_ratio). plain_ratio is only used to break ties."""
        h, v = _normalize(header), _normalize(valid)
        if not h:
            return 0, 0
        if h in self.aliases:
            # An alias is an explicit instruction for this header only. It scores just
            # below an exact match so it can never steal a name from a column that
            # already has it (which would leave two columns with the same name).
            return (ALIAS_SCORE, 100) if self.aliases[h] == valid else (0, 0)
        ratio = fuzz.ratio(h, v)
        score = float(ratio)
        if v in _tokens(header):                       # whole-word hit
            score = max(score, TOKEN_MATCH_SCORE)
        return score, ratio

    def match_columns(self) -> list[ColumnMatch]:
        """Match file columns to valid names; returns one ColumnMatch per column.

        Matching is one-to-one: every valid name is used at most once. All
        (column, valid name) pairs at or above the threshold are ranked by score and
        assigned greedily, with plain fuzzy ratio breaking score ties. For the supplied
        file, "BearCat" matches Bear and "Domestic Cat" matches Cat. Unmatched columns
        get matched=None.
        """
        columns = list(self.df.columns)
        candidates = []
        for pos, col in enumerate(columns):
            for valid in self.valid_columns:
                score, ratio = self._score(col, valid)
                if score >= self.threshold:
                    candidates.append((score, ratio, pos, valid))
        candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))

        assigned: dict[int, tuple[str, float]] = {}
        used: set[str] = set()
        for score, _, pos, valid in candidates:
            if pos not in assigned and valid not in used:
                assigned[pos] = (valid, score)
                used.add(valid)

        self._matches = [
            ColumnMatch(pos, col, *assigned.get(pos, (None, 0.0)))
            for pos, col in enumerate(columns)
        ]
        return self._matches

    # ---------------------------------------------------------------- mutation
    def rename_columns(self) -> dict[str, str]:
        """Rename matched columns to their valid names. Unmatched columns are left as-is.

        Returns {old_name: new_name} for columns that changed.
        """
        matches = self._matches or self.match_columns()
        new_names, changes = [], {}
        for m in matches:
            new = m.matched or m.original
            new_names.append(new)
            if new != m.original:
                changes[m.original] = new
        duplicates = sorted({n for n in new_names if n.strip() and new_names.count(n) > 1})
        if duplicates:
            log.warning('duplicate column names after rename: %s', duplicates)
        self.df.columns = new_names
        self._matches = None            # names changed, any cached match is stale
        return changes

    def drop_rows(self, row_numbers: list[int]) -> int:
        """Drop rows by row number; returns how many were removed.

        Uses the values of `row_column` if present (matched ignoring case and
        surrounding whitespace), otherwise the 1-based position of the data rows
        (header excluded). Row numbers that match nothing are logged, not ignored.
        """
        wanted = {str(n) for n in row_numbers}
        row_positions = [pos for pos, name in enumerate(self.df.columns)
                         if name.strip().casefold() == self.row_column.strip().casefold()]
        if len(row_positions) > 1:
            raise CSVRewriterError('Multiple columns match the row identifier')
        if row_positions:
            ids = self.df.iloc[:, row_positions[0]].str.strip()
        else:
            ids = pd.Series(range(1, len(self.df) + 1), index=self.df.index).astype(str)
        mask = ids.isin(wanted)

        missing = wanted - set(ids)
        if missing:
            log.warning('row numbers not found: %s', sorted(missing, key=int))

        self.df = self.df.loc[~mask].reset_index(drop=True)
        return int(mask.sum())

    def to_csv(self, out_file: str) -> None:
        self.df.to_csv(out_file, index=False, encoding='utf-8')


# ------------------------------------------------------------------------- CLI
def _parse_aliases(pairs: list[str]) -> dict[str, str]:
    aliases = {}
    for pair in pairs:
        header, sep, target = pair.partition('=')
        if not sep:
            raise CSVRewriterError(f'--alias expects HEADER=NAME, got {pair!r}')
        aliases[header] = target
    return aliases


def _open(args: argparse.Namespace, aliases: dict[str, str]) -> CSVRewriter:
    """Open the file. If the header won't parse as standard CSV, retry once with
    header-only backslash escaping (unless --strict), and say so."""
    def build(escape):
        return CSVRewriter(args.input, CARS, args.threshold,
                           header_escapechar=escape, aliases=aliases)
    if args.legacy_header:
        return build(LEGACY_ESCAPE)
    try:
        return build(None)
    except HeaderFormatError:
        if args.strict:
            raise
        log.warning('header is not standard CSV; retrying with backslash escaping '
                    '(header only; data values are untouched)')
        return build(LEGACY_ESCAPE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', default='input.csv')
    parser.add_argument('--output', default='output.csv')
    parser.add_argument('--drop', nargs='*', type=int, default=[3, 7],
                        help='row numbers to drop (default: 3 7, an example); no values = drop nothing')
    parser.add_argument('--threshold', type=int, default=DEFAULT_THRESHOLD,
                        help=f'minimum match score 0-100 (default {DEFAULT_THRESHOLD})')
    parser.add_argument('--alias', action='append', default=[], metavar='HEADER=NAME',
                        help='explicit header-to-valid-name mapping; repeatable')
    parser.add_argument('--legacy-header', action='store_true',
                        help='force backslash escaping in the header only (otherwise tried automatically)')
    parser.add_argument('--strict', action='store_true',
                        help='never retry automatically with backslash escaping')
    parser.add_argument('--dry-run', action='store_true',
                        help='print the column matches and exit without writing')
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.legacy_header and args.strict:
        parser.error('--legacy-header and --strict cannot be combined')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    try:
        if os.path.exists(args.output) and os.path.exists(args.input) \
                and os.path.samefile(args.input, args.output):
            raise CSVRewriterError('--output must differ from --input (refusing to overwrite the source)')
        rw = _open(args, _parse_aliases(args.alias))

        print('Column matches:')
        for m in rw.match_columns():
            if m.matched:
                flag = '  <- review' if m.score < REVIEW_BELOW else ''
                target = f'{m.matched} ({m.score:.0f}){flag}'
            else:
                target = 'NO MATCH - kept as-is'
            print(f'  [{m.position:>2}] {m.original!r:<20} -> {target}')
        if args.dry_run:
            return 0

        changes = rw.rename_columns()
        print(f'\nRenamed {len(changes)} column(s)')
        print(f'Dropped {rw.drop_rows(args.drop)} row(s)')
        rw.to_csv(args.output)
        print(f'Wrote {args.output}')
    except CSVRewriterError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
