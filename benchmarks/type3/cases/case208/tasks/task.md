# Enable strict type verification for CustomDirectiveSkipParser classes

## Description
We are working on enforcing strict type verification for the `sybil-extras` public interface. Currently, running type verifiers (specifically `pyright --verifytypes`) against our package fails due to issues with our `CustomDirectiveSkipParser` classes (found in Markdown, MyST, and reST parsers).

Relevant upstream discussion regarding the base classes involved can be found here: [simplistix/sybil#149](https://github.com/simplistix/sybil/pull/149).

Please investigate the verification failures and refactor the `CustomDirectiveSkipParser` implementation to resolve these errors. You should review the upstream context to determine the appropriate approach for handling these types in `sybil-extras`.

## Steps to Reproduce / Logs
Running strict type verification against the library produces the following errors:

```text
Symbols used in public interface:
sybil_extras.parsers.markdown.custom_directive_skip.CustomDirectiveSkipParser
   error: Type of base class "sybil.parsers.abstract.skip.AbstractSkipParser" is partially unknown
sybil.parsers.abstract.skip.AbstractSkipParser.lexers
  error: Type is missing type annotation and could be inferred differently by type checkers
    Inferred type is "LexerCollection"
sybil.parsers.abstract.skip.AbstractSkipParser.skipper
  error: Type is missing type annotation and could be inferred differently by type checkers
    Inferred type is "Skipper"
```

## Expected Behavior
The `CustomDirectiveSkipParser` classes (for Markdown, MyST, and reST) should be refactored such that `pyright --verifytypes sybil_extras` passes successfully. The classes must maintain their existing functionality while satisfying the strict type verification requirements.

The repository is at `/workspace/sybil-extras`, checked out at commit `ebfe2979183965b10b193ed9f7603f64de87def9`.