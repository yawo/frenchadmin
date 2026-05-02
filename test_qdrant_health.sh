#!/bin/bash
echo "=== Testing qdrant healthcheck from inside container ==="
docker exec qdrant_container bash -c '
  exec 3<>/dev/tcp/localhost/6333
  echo -e "GET /healthz HTTP/1.0\r\n\r\n" >&3
  sleep 1
  cat <&3
  echo "exit: $?"
'
echo ""
echo "=== Docker healthcheck log ==="
docker inspect qdrant_container --format '{{range .State.Health.Log}}Status: {{.ExitCode}} | Output: {{.Output}}{{end}}'
