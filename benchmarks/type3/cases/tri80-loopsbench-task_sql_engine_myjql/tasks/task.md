Implement five core modules of the MyJQL teaching database engine: an on-disk free-space hash map (`hash_map`), a paged table storage layer (`table`), a chunked string storage layer (`str`), a disk-backed B-Tree index (`b_tree`), and the top-level key/value API (`myjql`).

The C source tree lives under `/workspace/myjql/`. The five target files (`myjql/src/hash_map.c`, `myjql/src/table.c`, `myjql/src/str.c`, `myjql/src/b_tree.c`, `myjql/src/myjql.c`) currently contain compilable frameworks or stubs that you must turn into correct implementations. Public API contracts are defined in `myjql/include/hash_map.h`, `myjql/include/table.h`, `myjql/include/str.h`, `myjql/include/b_tree.h`, and `myjql/include/myjql.h`; do not change any declared public signatures. You may add or remove internal helper functions inside the five `.c` files.

Implement the following modules:

Free-space map (hash map) — `myjql/src/hash_map.c`
Implement `hash_table_init`, `hash_table_close`, `hash_table_insert`, `hash_table_pop_lower_bound`, `hash_table_pop`.
Data layout (`HashMapControlBlock`, `HashMapDirectoryBlock`, `HashMapBlock`) is defined in `myjql/include/hash_map.h`.
`hash_table_pop_lower_bound(pool, size)` pops and removes one `addr` from the smallest bucket whose `bucket_size >= size`; if no suitable entry exists, return `-1`.
`hash_table_insert` must reject duplicate `addr`.
`hash_table_pop` will only be called with an `addr` that exists.
Note: `myjql/src/table.c` depends on FSM correctness for `table_insert` / `table_delete`.

Table storage layer — `myjql/src/table.c`
Implement `table_get_total_blocks`, `table_block_get_total_items`, `table_read`, `table_insert`, `table_delete`.
The table owns a data file and an FSM file; `table_insert` must reuse a block from the FSM when possible, otherwise append a new `Block` page.
`table_read` copies the stored row bytes into `dest`.
`table_delete` must remove the row and update the FSM from the block's old free-space bucket to its new one.
`table_init`, `table_close`, and `print_rid` may be refactored but must keep their existing signatures.

Chunked string storage — `myjql/src/str.c`
Implement `read_string`, `has_next_char`, `next_char`, `compare_string_record`, `write_string`, `delete_string`, `load_string`.
Chunk layout and macros (including `STR_CHUNK_MAX_LEN`) are in `myjql/include/str.h`; chunks are linked by `RID` as a singly linked list.
`write_string` must support `size = 0`.
`load_string` must NOT append `\\0` automatically.
`compare_string_record` performs a lexicographic comparison.

B-Tree index — `myjql/src/b_tree.c`
Implement `b_tree_init`, `b_tree_close`, `b_tree_search`, `b_tree_insert`, `b_tree_delete`.
Follow the leaf/non-leaf node layout, split/merge/redistribution, and root-shrinking semantics in `myjql/include/b_tree.h`.
When writing keys into non-leaf nodes you must call `insert_handler` to copy keys. When deleting or replacing keys in non-leaf nodes, use `delete_handler` / `insert_handler` as described in the header comments.
If a key is not found, return an "empty RID" such that `get_rid_block_addr(rid) == -1`.

MyJQL API — `myjql/src/myjql.c`
Implement `myjql_init`, `myjql_close`, `myjql_get`, `myjql_set`, `myjql_del`.
Keep the B-Tree index, record table, and string table in sync for inserts, overwrites, lookups, and deletes.
`myjql_get` returns `-1` if the key does not exist.
`myjql_set` must overwrite any existing value for the key.
`myjql_del` must remove the key/value pair if it exists and do nothing otherwise.

Build expectations:

The project uses CMake. The top-level `CMakeLists.txt` at `/workspace/CMakeLists.txt` adds the `myjql` subdirectory. Standard build: `cmake -S /workspace -B /workspace/build -DCMAKE_BUILD_TYPE=Release && cmake --build /workspace/build`.
