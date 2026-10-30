# How I used AI on this assignment

I used AI as a coding partner on this one. It helped me move faster, but I made the design calls and checked every result by running the code, not by trusting what the assistant told me. Parts of the code and tests were written by AI.

For what the tool does and how to run it, see the [README](README.md). This file is only about how I worked with AI.

## Tools

- **Claude (chat):** looking at the input file, the first version of the code, benchmarking, and the final cleanup and tests.
- **OpenAI Codex:** code review, extra tests, and docs for one in-between version.

## What I asked AI to do

**Look at the data first.** Before writing any code, I had it print the raw bytes of `input.csv` and show how it parses. That's how I found the problems listed in the README under "Issues found in `input.csv`". None of them are mentioned in the task.

**Test the starter code.** I had it score each messy header against the valid names with `fuzz.ratio`. The starter's 80 cutoff only catches 3 of the 6 messy headers (`aligator`, `grafe`, ` Elephant`). `BearCat` scores 73, `Domestic Cat` 40, and the Iguana header 57, so those all get missed.

**Write the first draft.** The class, the CLI, error handling, the benchmark script, and first drafts of the README and this file. I edited all of it.

**Review and tests.** I asked it to check my solution against the assignment and then turned every problem it found into a test. There are 55 tests now.

## Where the AI got it wrong

- **Wrong matches.** An early version matched any header that contained a valid name, so `Catalog` became Cat, `Bearing` became Bear, and `Dogma` became Dog. The sample file hid this because `Domestic Cat` took Cat first. I switched to whole-word matching.
- **Data getting changed.** To read the odd Iguana header, the first version turned on backslash escaping for the whole file, so a value like `x\y` quietly turned into `xy`. Now escaping only applies to the header.
- **Two columns with the same name.** `BearCat` could match both Bear and Cat, and `Domestic Cat` also wanted Cat. I made matching one-to-one, so each valid name is used once.
- **A hardcoded fix.** One version special-cased `BearCat -> Bear` in the CLI. I tested without it, the sample came out the same, so I took it out. It was just fitting the code to one header.
- **An alias bug.** If a file had both `BearCat` and `Bear`, the alias could give two columns the name `Bear`. I fixed it so an alias can never take a name from an exact match.
- **The first run crashed.** `python main.py` threw a raw `_csv.Error` on the supplied file unless you knew to pass a flag. A reviewer would hit that straight away, so now it retries the header on its own, prints a warning, and carries on.
- **Slow header detection.** The generated code scanned every row to find the header. Fine on 12 rows, very slow on a million. I timed it and replaced it. `benchmark.py` lets you reproduce the timing.
- **My own bug.** One of my new tests showed that error messages gave the wrong line number, off by one. I fixed that too.

## Decisions I made myself

- **Which rows to drop.** The task doesn't say, so I drop by the value in the `row` column (it's shuffled, so position would remove the wrong rows). Rows 3 and 7 are only an example.
- **The two blank headers.** I left them blank instead of guessing Frog and Jaguar.
- **Unmatched columns stay as they are.** I don't delete or guess.
- **Checking results.** I looked at every mapping in the output against the input file, and ran extra checks on tricky headers, tricky values, and duplicate names.

## Risks

The biggest risk with AI is code that looks right but quietly changes data or picks the wrong column. Four of the problems above (the wrong substring matches, the backslash change, the duplicate alias name, and the slow scan) all passed on the 12-row sample and only showed up when I tried inputs the sample doesn't have. To guard against that, there's no AI in the data path when the tool runs, matching scores are visible, anything under 90 is flagged `<- review`, and the tests compare real values, not just file shapes.

## What I'd do with AI next

- Generate nastier test files (unicode, duplicate headers, other delimiters).
- Review changes for the kind of silent data problems listed above.
- Suggest alias lists, as config I review by hand, never as automatic renames.
