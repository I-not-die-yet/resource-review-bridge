# Contributing

Focused bug reports and small pull requests are welcome.

Before submitting a change:

1. keep the public MCP surface limited to read-only resource review;
2. do not add login, posting, general shell, or arbitrary filesystem capabilities;
3. run `python3 -m unittest discover -s tests -v`;
4. include a focused regression test for behavior changes;
5. remove captured content, local paths, keys, Tunnel profiles, state databases, and logs.

Use GitHub's private vulnerability reporting for security issues. Do not put real secrets or private source material in issues or pull requests.
