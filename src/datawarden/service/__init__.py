"""The system's four operations, below any transport.

`mcp` and `http` are sibling layers and may not import each other (enforced by
`lint-imports`). What neither transport should own lives here: what `run_query`
does, what `describe_table` returns and the shape of a response. `mcp/` keeps the
protocol (`tools/list`, `server/discover`, `resultType`, JSON schemas) and `http/`
keeps the routes. Both transports return byte-identical answers to the same request.
"""
