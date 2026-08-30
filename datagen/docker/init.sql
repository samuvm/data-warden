-- Build the catalogue against whatever is mounted at /warehouse/data, then show it.
--
-- In memory and from a script, rather than from a shipped database file. The
-- views are rebuilt on every start, so the shell can never be looking at a
-- catalogue that describes a previous build of the data -- and there is no
-- writable database anywhere in the container, which is the read-only guarantee
-- this shell is supposed to give.
.bail on
.read /etc/cierzo/catalog.sql
.mode duckbox
.prompt 'cierzo> '
SELECT view_name AS "table or view" FROM duckdb_views() WHERE NOT internal ORDER BY 1;
