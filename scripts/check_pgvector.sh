#!/usr/bin/env bash
# Verify pgvector is installed AND actually functional.
# Non-destructive: works on a TEMP table that vanishes when the session ends.
#
#   bash scripts/check_pgvector.sh
#
# Override container/db settings:
#   CONTAINER=myaibuddy-db PGUSER=myaibuddy PGDATABASE=myaibuddy bash scripts/check_pgvector.sh

set -uo pipefail

CONTAINER="${CONTAINER:-myaibuddy-db}"
PGUSER="${PGUSER:-myaibuddy}"
PGDATABASE="${PGDATABASE:-myaibuddy}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "FAIL: container '$CONTAINER' is not running" >&2
  exit 1
fi

# -t tuples only, -A unaligned, -q quiet (suppresses command tags)
q() {
  docker exec -i "$CONTAINER" psql -U "$PGUSER" -d "$PGDATABASE" \
    -v ON_ERROR_STOP=1 -tAq -c "$1" 2>&1
}

pass=0
fail=0

check() {
  local name="$1" result="$2" expected="$3"
  if [ "$result" = "$expected" ]; then
    printf '  \033[32mPASS\033[0m  %-32s %s\n' "$name" "$result"
    pass=$((pass + 1))
  else
    printf '  \033[31mFAIL\033[0m  %-32s got=[%s] want=[%s]\n' "$name" "$result" "$expected"
    fail=$((fail + 1))
  fi
}

echo "pgvector check -> ${CONTAINER} / ${PGDATABASE}"
echo

echo "1. extension installed"
check "extension present" \
  "$(q "SELECT count(*) FROM pg_extension WHERE extname='vector';")" "1"
ver="$(q "SELECT extversion FROM pg_extension WHERE extname='vector';")"
printf '       version: %s\n' "${ver:-<none>}"

echo
echo "2. type and distance operators"
check "vector type exists" \
  "$(q "SELECT count(*) FROM pg_type WHERE typname='vector';")" "1"
for op in '<=>' '<->' '<#>'; do
  check "operator ${op}" \
    "$(q "SELECT count(DISTINCT oprname) FROM pg_operator WHERE oprname='${op}';")" "1"
done

echo
echo "3. parsing and dimension handling"
check "parses 3-dim literal" \
  "$(q "SELECT '[1,2,3]'::vector::text;")" "[1,2,3]"
check "vector_dims()" \
  "$(q "SELECT vector_dims('[1,2,3]'::vector);")" "3"
# negative test: a 2-dim value must be rejected by a vector(3) column
dim_err="$(q "CREATE TEMP TABLE _pgvdim(v vector(3)); INSERT INTO _pgvdim VALUES ('[1,2]');")"
if printf '%s' "$dim_err" | grep -qi 'expected\|dimension'; then
  printf '  \033[32mPASS\033[0m  %-32s %s\n' "rejects wrong dimensions" "yes"
  pass=$((pass + 1))
else
  printf '  \033[31mFAIL\033[0m  %-32s got=[%s]\n' "rejects wrong dimensions" "$dim_err"
  fail=$((fail + 1))
fi

echo
echo "4. functional similarity search (temp table, auto-cleaned)"
res="$(q "CREATE TEMP TABLE _pgv(label text, v vector(3));
          INSERT INTO _pgv VALUES ('cats','[1,0,0]'),('dogs','[0,1,0]'),('birds','[0,0,1]');
          CREATE INDEX ON _pgv USING hnsw (v vector_cosine_ops);
          SELECT label FROM _pgv ORDER BY v <=> '[1,0,0]'::vector LIMIT 1;")"
check "nearest neighbour of [1,0,0]" "$(echo "$res" | tail -1)" "cats"

check "self distance is zero" \
  "$(q "SELECT ('[1,0,0]'::vector <=> '[1,0,0]'::vector) = 0;")" "t"
check "ranking is correct" \
  "$(q "SELECT ('[1,0,0]'::vector <=> '[1,0,0]'::vector)
             < ('[0,1,0]'::vector <=> '[1,0,0]'::vector);")" "t"

echo
echo "5. ANN index support"
check "hnsw access method" \
  "$(q "SELECT count(*) FROM pg_am WHERE amname='hnsw';")" "1"
check "ivfflat access method" \
  "$(q "SELECT count(*) FROM pg_am WHERE amname='ivfflat';")" "1"

echo
echo "6. app schema (informational)"
q "SELECT '   ' || c.relname || '.' || a.attname || '  ' || format_type(a.atttypid, a.atttypmod)
   FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
   WHERE a.atttypid='vector'::regtype AND a.attnum>0 AND NOT a.attisdropped
   ORDER BY 1;" || true
q "SELECT '   index: ' || indexname FROM pg_indexes WHERE indexdef ILIKE '%hnsw%';"

echo
if [ "$fail" -eq 0 ]; then
  printf '\033[32mAll %d checks passed - pgvector is working.\033[0m\n' "$pass"
else
  printf '\033[31m%d passed, %d FAILED.\033[0m\n' "$pass" "$fail"
  exit 1
fi
