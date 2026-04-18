#!/bin/bash

# Load environment variables from .env file
export $(grep -v '^#' .env | xargs)

JWT_TOKEN=$JWT_TOKEN
AIRFLOW_API="http://127.0.0.1:8080/api/v2"

# Function to generate new JWT token
generate_jwt_token() {
    echo "Generating new JWT token..."
    
    RESPONSE=$(curl -s -X 'POST' \
   'http://127.0.0.1:8080/auth/token' \
   -H 'Content-Type: application/json' \
   -d "{\"username\": \"${_AIRFLOW_WWW_USER_USERNAME}\", \"password\": \"${_AIRFLOW_WWW_USER_PASSWORD}\"}")
    
    NEW_TOKEN=$(echo "$RESPONSE" | jq -r '.access_token')
    
    if [ "$NEW_TOKEN" != "null" ] && [ -n "$NEW_TOKEN" ]; then
        # Update JWT_TOKEN in .env file
        if grep -q "^JWT_TOKEN=" .env; then
            # Replace existing JWT_TOKEN line
            sed -i "s/^JWT_TOKEN=.*/JWT_TOKEN=$NEW_TOKEN/" .env
        else
            printf "\n" >> .env
            echo "JWT_TOKEN=$NEW_TOKEN" >> .env
        fi
        
        # Export the new token for current script execution
        export JWT_TOKEN=$NEW_TOKEN
        echo "JWT token updated successfully"
        return 0
    else
        echo "Failed to generate JWT token. Response: $RESPONSE"
        return 1
    fi
}

# Test if current token is valid (if it exists) by testing the actual endpoint we'll use
if [ -n "$JWT_TOKEN" ]; then
    echo "Testing current JWT token validity..."
    TEST_RESPONSE=$(curl -s -w "%{http_code}" -X GET "$AIRFLOW_API/dags" -H "Authorization: Bearer $JWT_TOKEN" -o /dev/null)
    
    if [ "$TEST_RESPONSE" != "200" ]; then
        echo "Current JWT token is invalid or expired (HTTP $TEST_RESPONSE)"
        generate_jwt_token || exit 1
    else
        echo "Current JWT token is valid"
    fi
else
    echo "No JWT token found in .env file"
    generate_jwt_token || exit 1
fi

# Check if at least a DAG is running (1) or not (0)
RUNNING_FOUND=0

# Listing all dags
DAGS=$(curl -s -X GET "$AIRFLOW_API/dags" -H "Authorization: Bearer $JWT_TOKEN" | jq -r '.dags[].dag_id')

if [ -n "$DAGS" ]; then
    for dag_id in $DAGS; do
        RUNNING=$(curl -s -X GET "$AIRFLOW_API/dags/$dag_id/dagRuns" \
            -H "Authorization: Bearer $JWT_TOKEN" \
            -G \
            --data-urlencode "state=running" | jq -r '.total_entries')

        if [ "$RUNNING" -gt 0 ]; then
            echo "DAG $dag_id is RUNNING ($RUNNING runs)"
            RUNNING_FOUND=1
        fi
    done
fi

if [ "$RUNNING_FOUND" -eq 0 ]; then
  echo "No DAGs are currently running. Deploying..."
  exit 0
else
  echo "Some DAGs are still running. Deployment aborted."
  exit 1
fi
