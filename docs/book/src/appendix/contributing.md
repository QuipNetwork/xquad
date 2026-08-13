# Contributing to These Docs

This book is written and reviewed in the same repository as the code it
documents. If something here is wrong, incomplete, or out of date, the
fix goes through the same review process as a code change. Start from
[`CONTRIBUTING.md`](https://gitlab.com/quip.network/xquad/-/blob/main/CONTRIBUTING.md)
for the general contribution rules -- sign-off, commit format, and
review expectations apply to documentation changes too.

## Where the Pages Live

Every page you can read here comes from one Markdown file under
[`docs/book/src/`](https://gitlab.com/quip.network/xquad/-/tree/main/docs/book/src).
The URL mirrors the path: this page is `appendix/contributing.md`.

Two files control the shape of the book rather than its content:

- `docs/book/src/SUMMARY.md` is the table of contents and the sidebar.
  Every page must be linked from it, and every link in it must point at
  a page that exists. Continuous integration checks both directions, so
  adding a new page without adding its `SUMMARY.md` entry fails the
  build rather than producing an orphan.
- `book.toml`, at the repository root, holds the renderer configuration:
  the theme, the output directory, and the preprocessor list.

## Pages You Should Not Edit by Hand

Sixteen pages are generated, not written. Editing one of them directly
works until the next regeneration silently reverts it. Each generated
file opens with a `DO NOT EDIT` banner as its first line, which you will
see immediately if you open it in an editor.

They are the [instruction reference](../xqvm/opcodes.md), generated from
`conformance/opcodes.yaml`, and the fifteen
[example pages](../examples/), generated from
`examples/manifest.yaml` together with each example's own `README.md`.

To change one, edit its source and regenerate:

```sh
make regen-docs
```

Continuous integration runs the same generators in check mode and fails
if the committed pages differ from freshly generated output, so a
regenerated page has to be committed alongside the source that produced
it.

## Building the Book Locally

The renderer and its diagram preprocessor are pinned in
`scripts/cargo-tools.lock` and installed by `make deps`. With those in
place:

```sh
make build-docs   # build once into docs/book/build/
make serve-docs   # rebuild on save and open a browser
```

`make build-docs` also asserts that every diagram in the sources actually
rendered, which catches a preprocessor failure that would otherwise show
up as a page of raw diagram source.

Two more checks are worth running before you open a merge request:

```sh
make check-docs-generated   # generated pages match their sources
make check-docs-drift       # table-of-contents coverage and prose guards
```

Both run in continuous integration as well, so running them locally just
saves you a round trip.

## Reporting Something Instead

Not every problem is worth a merge request. If you have found an error
but not the fix, or you want to argue that a page should exist at all,
[open an issue](https://gitlab.com/quip.network/xquad/-/issues) and say
which page you mean.
