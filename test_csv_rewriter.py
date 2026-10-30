import csv
import logging
from pathlib import Path

import pytest

from main import (CARS, CSVRewriter, CSVRewriterError, HeaderFormatError, main)

HERE = Path(__file__).parent
SAMPLE = str(HERE / 'input.csv')
BS = chr(92)


@pytest.fixture
def rw():
    """The supplied file, parsed with the explicit legacy-header option, no aliases."""
    return CSVRewriter(SAMPLE, CARS, header_escapechar=BS)


def write(tmp_path, text, name='f.csv'):
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return str(path)


# ---------------------------------------------------------------- loading
def test_header_row_found_past_blank_line(rw):
    assert list(rw.df.columns)[0] == 'row' and len(rw.df) == 12


def test_utf8_bom_is_ignored(tmp_path):
    path = tmp_path / 'bom.csv'
    path.write_bytes(b'\xef\xbb\xbfDog,Cat\n1,2\n')
    assert list(CSVRewriter(str(path), CARS).df.columns) == ['Dog', 'Cat']


def test_header_only_file_has_no_rows(tmp_path):
    rewriter = CSVRewriter(write(tmp_path, 'row,Dog\n'), CARS)
    assert len(rewriter.df) == 0
    assert rewriter.drop_rows([1]) == 0          # must not crash on an empty body


@pytest.mark.parametrize('text', ['', ',,,\n,,,\n'])
def test_file_without_content_is_rejected(tmp_path, text):
    with pytest.raises(CSVRewriterError, match='no data'):
        CSVRewriter(write(tmp_path, text), CARS)


def test_missing_file_is_a_friendly_error():
    with pytest.raises(CSVRewriterError, match='file not found'):
        CSVRewriter('does-not-exist.csv', CARS)


def test_non_utf8_file_is_a_friendly_error(tmp_path):
    path = tmp_path / 'latin.csv'
    path.write_bytes(b'Dog,Cat\n\xe9,2\n')
    with pytest.raises(CSVRewriterError, match='UTF-8'):
        CSVRewriter(str(path), CARS)


def test_record_width_mismatch_reports_line(tmp_path):
    with pytest.raises(CSVRewriterError, match=r'line 3: expected 2 fields, found 3'):
        CSVRewriter(write(tmp_path, 'Dog,Cat\n1,2\n1,2,3\n'), CARS)


def test_legacy_header_requires_explicit_option():
    with pytest.raises(HeaderFormatError):
        CSVRewriter(SAMPLE, CARS)


def test_unclosed_header_is_rejected(tmp_path):
    with pytest.raises(HeaderFormatError):
        CSVRewriter(write(tmp_path, 'Dog,"unfinished\n1,x\n'), CARS)


def test_malformed_body_is_not_a_header_error(tmp_path):
    with pytest.raises(CSVRewriterError) as err:
        CSVRewriter(write(tmp_path, 'Dog,Cat\n1,"x"y\n'), CARS)
    assert not isinstance(err.value, HeaderFormatError)


# --------------------------------------------------------------- data safety
def test_literal_backslashes_and_quoted_values_roundtrip(tmp_path):
    path = tmp_path / 'special.csv'
    values = [[r'C:\new\file', 'comma, quote " and newline\nkept']]
    with path.open('w', newline='') as target:
        writer = csv.writer(target)
        writer.writerow(['Dog', 'Cat'])
        writer.writerows(values)
    rewriter = CSVRewriter(str(path), CARS)
    assert rewriter.df.values.tolist() == values
    out = tmp_path / 'output.csv'
    rewriter.to_csv(str(out))
    assert CSVRewriter(str(out), CARS).df.values.tolist() == values


def test_legacy_header_does_not_change_body_backslashes(tmp_path):
    path = tmp_path / 'legacy.csv'
    header = Path(SAMPLE).read_text().splitlines()[1]
    values = [r'C:\new\file'] * 12
    with path.open('w', newline='') as target:
        target.write(header + '\n')
        csv.writer(target).writerow(values)
    rewriter = CSVRewriter(str(path), CARS, header_escapechar=BS)
    assert rewriter.df.values.tolist() == [values]
    assert rewriter.match_columns()[9].matched == 'Iguana'


def test_standard_header_ending_in_backslash_keeps_data(tmp_path):
    rewriter = CSVRewriter(write(tmp_path, 'Dog,"path' + BS + '"\n1,x\n'), CARS)
    assert list(rewriter.df.columns) == ['Dog', 'path' + BS]
    assert rewriter.df.values.tolist() == [['1', 'x']]


def test_leading_zeros_are_preserved(tmp_path):
    rewriter = CSVRewriter(write(tmp_path, 'row,Dog\n007,1\n'), CARS)
    assert rewriter.df['row'].tolist() == ['007']


# ---------------------------------------------------------------- matching
def test_match_columns(rw):
    got = {m.original: m.matched for m in rw.match_columns()}
    assert got['aligator'] == 'Alligator'
    assert got['BearCat'] == 'Bear'              # no alias needed: CamelCase tokens + one-to-one
    assert got['Domestic Cat'] == 'Cat'
    assert got[' Elephant'] == 'Elephant'        # original whitespace preserved
    assert got['grafe'] == 'Giraffe'
    assert got['lizard,"Iguana"'] == 'Iguana'
    assert got['row'] is None and got[''] is None


def test_match_results_expose_position_and_score(rw):
    first = rw.match_columns()[1]
    assert (first.position, first.original, first.matched) == (1, 'aligator', 'Alligator')
    assert 80 <= first.score <= 100


def test_each_valid_name_used_once(rw):
    names = [m.matched for m in rw.match_columns() if m.matched]
    assert len(names) == len(set(names))


def test_one_to_one_resolves_bearcat_vs_domestic_cat(tmp_path):
    for order in ('BearCat,Domestic Cat', 'Domestic Cat,BearCat'):
        rewriter = CSVRewriter(write(tmp_path, order + '\n1,2\n'), CARS)
        got = {m.original: m.matched for m in rewriter.match_columns()}
        assert got == {'BearCat': 'Bear', 'Domestic Cat': 'Cat'}


@pytest.mark.parametrize('header', ['Catalog', 'Bearing', 'Dogma', 'education', 'concatenate', 'hotdog'])
def test_unrelated_substrings_are_not_matches(tmp_path, header):
    rewriter = CSVRewriter(write(tmp_path, header + '\n1\n'), CARS)
    assert rewriter.match_columns()[0].matched is None


def test_threshold_is_respected(tmp_path):
    path = write(tmp_path, 'grafe\n1\n')
    assert CSVRewriter(path, CARS, threshold=90).match_columns()[0].matched is None
    assert CSVRewriter(path, CARS, threshold=80).match_columns()[0].matched == 'Giraffe'


def test_accents_and_case_are_ignored(tmp_path):
    rewriter = CSVRewriter(write(tmp_path, 'CAFÉ,Éléphant\n1,2\n'), ['Cafe', 'Elephant'])
    assert [m.matched for m in rewriter.match_columns()] == ['Cafe', 'Elephant']


def test_original_headers_preserved_until_rename(tmp_path):
    rewriter = CSVRewriter(write(tmp_path, ' Elephant , unknown \n1,2\n'), CARS)
    matches = rewriter.match_columns()
    assert [m.original for m in matches] == [' Elephant ', ' unknown ']
    assert matches[0].matched == 'Elephant'
    rewriter.rename_columns()
    assert list(rewriter.df.columns) == ['Elephant', ' unknown ']


def test_rename_leaves_unmatched_alone(rw):
    changes = rw.rename_columns()
    cols = list(rw.df.columns)
    assert 'Iguana' in cols and 'row' in cols and cols.count('') == 2
    assert changes['aligator'] == 'Alligator' and 'Dog' not in changes


def test_invalid_constructor_arguments():
    with pytest.raises(CSVRewriterError):
        CSVRewriter(SAMPLE, [], header_escapechar=BS)
    with pytest.raises(CSVRewriterError):
        CSVRewriter(SAMPLE, CARS, threshold=101, header_escapechar=BS)


# ----------------------------------------------------------------- aliases
@pytest.mark.parametrize('header', ['BearCat', 'bearcat', 'BEARCAT', ' Bear Cat '])
def test_explicit_alias_is_case_and_space_insensitive(tmp_path, header):
    rewriter = CSVRewriter(write(tmp_path, header + ',Domestic Cat\n1,2\n'), CARS,
                           aliases={'BearCat': 'Bear'})
    assert [m.matched for m in rewriter.match_columns()] == ['Bear', 'Cat']


def test_alias_overrides_fuzzy_score(tmp_path):
    rewriter = CSVRewriter(write(tmp_path, 'critter\n1\n'), CARS, aliases={'critter': 'Frog'})
    assert rewriter.match_columns()[0].matched == 'Frog'


def test_alias_never_steals_a_name_from_an_exact_column(tmp_path):
    """Previously an alias could win the tie and leave two columns named Bear."""
    for order in ('BearCat,Bear', 'Bear,BearCat'):
        rewriter = CSVRewriter(write(tmp_path, order + '\n1,2\n'), CARS, aliases={'BearCat': 'Bear'})
        rewriter.rename_columns()
        names = list(rewriter.df.columns)
        assert len(set(names)) == len(names)


@pytest.mark.parametrize('aliases, message', [
    ({'x': 'Unicorn'}, 'not a valid column name'),
    ({'dog': 'Cat'}, 'itself a valid column name'),
    ({'   ': 'Cat'}, 'must not be empty'),
    ({'BearCat': 'Bear', 'bear cat': 'Cat'}, 'conflicting aliases'),
])
def test_invalid_aliases_are_rejected(tmp_path, aliases, message):
    with pytest.raises(CSVRewriterError, match=message):
        CSVRewriter(write(tmp_path, 'Dog\n1\n'), CARS, aliases=aliases)


def test_duplicate_output_names_are_logged(tmp_path, caplog):
    rewriter = CSVRewriter(write(tmp_path, 'Dog,Dog\n1,2\n'), CARS)
    with caplog.at_level(logging.WARNING, logger='csvrewriter'):
        rewriter.rename_columns()
    assert 'duplicate column names' in caplog.text


# --------------------------------------------------------------- drop_rows
def test_drop_rows_uses_row_column_not_position(rw):
    assert rw.drop_rows([2, 12]) == 2
    assert not {'2', '12'} & set(rw.df['row'])


def test_drop_rows_warns_on_unknown_row(rw, caplog):
    with caplog.at_level(logging.WARNING, logger='csvrewriter'):
        assert rw.drop_rows([99]) == 0
    assert 'not found' in caplog.text and len(rw.df) == 12


def test_drop_rows_positional_fallback(tmp_path):
    rewriter = CSVRewriter(write(tmp_path, 'Dog,Cat\n1,1\n2,2\n3,3\n'), CARS)
    assert rewriter.drop_rows([2]) == 1 and list(rewriter.df['Dog']) == ['1', '3']


def test_spaced_row_identifier_uses_values_not_positions(tmp_path):
    rewriter = CSVRewriter(write(tmp_path, ' RoW ,Dog\n2,a\n1,b\n'), CARS)
    rewriter.rename_columns()
    assert rewriter.drop_rows([1]) == 1
    assert list(rewriter.df.columns) == [' RoW ', 'Dog']
    assert rewriter.df.values.tolist() == [['2', 'a']]


def test_ambiguous_row_identifiers_rejected_before_deletion(tmp_path):
    rewriter = CSVRewriter(write(tmp_path, 'row, ROW \n1,2\n'), CARS)
    with pytest.raises(CSVRewriterError, match='Multiple columns'):
        rewriter.drop_rows([1])
    assert len(rewriter.df) == 1


# ------------------------------------------------------------------ output
def test_to_csv_roundtrip(rw, tmp_path):
    rw.rename_columns()
    expected_header = list(rw.df.columns)
    expected_rows = rw.df.loc[~rw.df['row'].isin(['3', '7'])].values.tolist()
    assert rw.drop_rows([3, 7]) == 2
    out = tmp_path / 'o.csv'
    rw.to_csv(str(out))
    with out.open(newline='') as source:
        assert list(csv.reader(source)) == [expected_header] + expected_rows
    reloaded = CSVRewriter(str(out), CARS)
    assert list(reloaded.df.columns) == expected_header
    assert reloaded.df.values.tolist() == expected_rows


# --------------------------------------------------------------------- CLI
def test_cli_first_run_needs_no_flags_and_matches_golden_output(tmp_path, caplog):
    """`python main.py` on the supplied file must just work and reproduce output.csv."""
    out = tmp_path / 'out.csv'
    with caplog.at_level(logging.WARNING):
        code = main(['--input', SAMPLE, '--output', str(out)])
    assert code == 0
    assert out.read_text() == (HERE / 'output.csv').read_text()
    assert 'retrying with backslash escaping' in caplog.text


def test_cli_strict_gives_friendly_error(tmp_path, capsys):
    code = main(['--input', SAMPLE, '--output', str(tmp_path / 'o.csv'), '--strict'])
    assert code == 2
    assert capsys.readouterr().err.startswith('error:')
    assert not (tmp_path / 'o.csv').exists()


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    out = tmp_path / 'o.csv'
    assert main(['--input', SAMPLE, '--output', str(out), '--dry-run']) == 0
    text = capsys.readouterr().out
    assert 'Giraffe (83)  <- review' in text and not out.exists()


def test_cli_drop_without_values_drops_nothing(tmp_path):
    out = tmp_path / 'o.csv'
    assert main(['--input', SAMPLE, '--output', str(out), '--drop']) == 0
    assert len(out.read_text().splitlines()) == 13      # header + 12 rows


def test_cli_missing_input_is_friendly(tmp_path, capsys):
    assert main(['--input', str(tmp_path / 'nope.csv')]) == 2
    assert 'file not found' in capsys.readouterr().err


def test_cli_refuses_to_overwrite_input(tmp_path, capsys):
    path = write(tmp_path, 'Dog\n1\n')
    assert main(['--input', path, '--output', path]) == 2
    assert Path(path).read_text() == 'Dog\n1\n'


def test_cli_alias_option(tmp_path):
    src = write(tmp_path, 'critter,row\n1,1\n')
    out = tmp_path / 'o.csv'
    assert main(['--input', src, '--output', str(out), '--alias', 'critter=Frog', '--drop']) == 0
    assert out.read_text().splitlines()[0] == 'Frog,row'


def test_cli_bad_alias_syntax_is_friendly(tmp_path, capsys):
    src = write(tmp_path, 'Dog\n1\n')
    assert main(['--input', src, '--output', str(tmp_path / 'o.csv'), '--alias', 'oops']) == 2
    assert 'HEADER=NAME' in capsys.readouterr().err
