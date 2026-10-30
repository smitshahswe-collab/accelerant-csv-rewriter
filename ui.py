"""Streamlit front end for CSVRewriter.  Run with:  streamlit run ui.py

It only calls the public CSVRewriter methods from main.py, so the UI and the CLI
always behave the same way.
"""
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from main import (CARS, LEGACY_ESCAPE, REVIEW_BELOW, CSVRewriter,
                  CSVRewriterError, HeaderFormatError)

SAMPLE = Path(__file__).with_name('input.csv')

st.set_page_config(page_title='CSV Rewriter', page_icon=':broom:', layout='wide')
st.title('CSV Rewriter')
st.caption('Match messy headers to a known-good list, rename them, drop rows, download the result.')


def open_rewriter(path: str, valid: list[str], threshold: int, aliases: dict[str, str]):
    """Standard parsing first; if the header is not valid CSV, retry with header-only
    backslash escaping (same behaviour as the CLI). Returns (rewriter, warning)."""
    try:
        return CSVRewriter(path, valid, threshold, aliases=aliases), None
    except HeaderFormatError:
        rw = CSVRewriter(path, valid, threshold, header_escapechar=LEGACY_ESCAPE, aliases=aliases)
        return rw, 'Header is not standard CSV, so it was re-read with backslash escaping (header only; data untouched).'


def parse_aliases(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if '=' in line:
            key, _, val = line.partition('=')
            out[key.strip()] = val.strip()
    return out


def for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Streamlit needs unique column labels; blank or repeated headers get a display-only label."""
    seen, labels = {}, []
    for i, name in enumerate(df.columns, 1):
        label = name if name.strip() else f'(blank {i})'
        seen[label] = seen.get(label, 0) + 1
        labels.append(label if seen[label] == 1 else f'{label} #{seen[label]}')
    shown = df.copy()
    shown.columns = labels
    return shown


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header('Settings')
    upload = st.file_uploader('CSV file', type=['csv'])
    use_sample = st.checkbox('Use the sample input.csv', value=upload is None, disabled=upload is not None)
    valid_text = st.text_area('Valid column names (one per line)', '\n'.join(CARS), height=230)
    threshold = st.slider('Match threshold', 0, 100, 80,
                          help='Minimum similarity score to accept a match.')
    alias_text = st.text_area('Aliases (optional, HEADER=Name per line)', placeholder='critter=Frog')

valid = [v.strip() for v in valid_text.splitlines() if v.strip()]

if upload is not None:
    tmp = Path(tempfile.mkdtemp()) / 'upload.csv'
    tmp.write_bytes(upload.getvalue())
    path = str(tmp)
elif use_sample and SAMPLE.exists():
    path = str(SAMPLE)
else:
    st.info('Upload a CSV, or tick "Use the sample input.csv", to get started.')
    st.stop()

try:
    rewriter, warning = open_rewriter(path, valid, threshold, parse_aliases(alias_text))
except CSVRewriterError as exc:
    st.error(f'Could not read the file: {exc}')
    st.stop()

if warning:
    st.warning(warning)

raw = rewriter.df.copy()

# ------------------------------------------------------------ 1. match table
st.subheader('1. Column matches')
matches = rewriter.match_columns()
table = pd.DataFrame([{
    'Position': m.position + 1,
    'Original header': repr(m.original) if m.original != m.original.strip() or not m.original else m.original,
    'Matched to': m.matched or '(no match, kept as-is)',
    'Score': round(m.score),
    'Status': ('No match' if m.matched is None
               else 'Review' if m.score < REVIEW_BELOW
               else 'OK'),
} for m in matches])


def colour(row):
    palette = {'OK': '#1f6f3f33', 'Review': '#b8860b44', 'No match': '#8b222244'}
    return [f'background-color: {palette[row["Status"]]}'] * len(row)


st.dataframe(table.style.apply(colour, axis=1), hide_index=True, width='stretch',
             height=36 * (len(table) + 1) + 3)
ok = sum(m.matched is not None for m in matches)
review = sum(m.matched is not None and m.score < REVIEW_BELOW for m in matches)
c1, c2, c3 = st.columns(3)
c1.metric('Columns matched', f'{ok} / {len(matches)}')
c2.metric('Need a human look', review)
c3.metric('Unmatched (kept as-is)', len(matches) - ok)

# ------------------------------------------------------------ 2. drop rows
st.subheader('2. Rows to drop')
row_cols = [c for c in raw.columns if c.strip().casefold() == 'row']
if row_cols:
    ids = list(dict.fromkeys(raw[row_cols[0]].str.strip()))
    st.caption('Uses the file\'s `row` column.')
else:
    ids = [str(i) for i in range(1, len(raw) + 1)]
    st.caption('No `row` column, so these are 1-based positions of the data rows.')
default = [i for i in ('3', '7') if i in ids]
to_drop = st.multiselect('Row numbers', ids, default=default)

# ------------------------------------------------------------ 3. apply
changes = rewriter.rename_columns()
dropped = rewriter.drop_rows([int(i) for i in to_drop])
result = rewriter.df

st.subheader('3. Result')
left, right = st.columns(2)
left.markdown(f'**Before** ({len(raw)} rows)')
left.dataframe(for_display(raw), hide_index=True, width='stretch')
right.markdown(f'**After** ({len(result)} rows, {dropped} dropped, {len(changes)} columns renamed)')
right.dataframe(for_display(result), hide_index=True, width='stretch')

out = Path(tempfile.mkdtemp()) / 'output.csv'
rewriter.to_csv(str(out))
st.download_button('Download output.csv', out.read_bytes(), file_name='output.csv', mime='text/csv',
                   type='primary')
