#!/bin/bash

# Script to manage checkpoint files for data processing
# Usage: 
#   ./manage_checkpoint.sh list          - List all checkpoint files
#   ./manage_checkpoint.sh view <file>   - View checkpoint details
#   ./manage_checkpoint.sh delete <file> - Delete a checkpoint file
#   ./manage_checkpoint.sh clean         - Delete all checkpoint files
#   ./manage_checkpoint.sh stats         - Show statistics about checkpoints

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$PROJECT_DIR/data/unprocessed"

case "${1:-}" in
    list)
        echo "=== Checkpoint files found ==="
        find "$DATA_DIR" -name "*.checkpoint" 2>/dev/null || echo "No checkpoint files found"
        ;;
    
    view)
        if [ -z "$2" ]; then
            echo "Error: Please provide checkpoint file path"
            echo "Usage: $0 view <checkpoint_file>"
            exit 1
        fi
        
        if [ ! -f "$2" ]; then
            echo "Error: Checkpoint file not found: $2"
            exit 1
        fi
        
        echo "=== Checkpoint details for: $2 ==="
        cat "$2" | python3 -m json.tool
        ;;
    
    delete)
        if [ -z "$2" ]; then
            echo "Error: Please provide checkpoint file path"
            echo "Usage: $0 delete <checkpoint_file>"
            exit 1
        fi
        
        if [ ! -f "$2" ]; then
            echo "Error: Checkpoint file not found: $2"
            exit 1
        fi
        
        rm -f "$2"
        echo "Checkpoint file deleted: $2"
        ;;
    
    clean)
        echo "=== Cleaning all checkpoint files ==="
        count=$(find "$PROJECT_DIR" -name "*.checkpoint" 2>/dev/null | wc -l)
        if [ "$count" -eq 0 ]; then
            echo "No checkpoint files found"
        else
            find "$PROJECT_DIR" -name "*.checkpoint" -delete 2>/dev/null
            echo "All $count checkpoint files have been deleted"
        fi
        ;;
    
    stats) 
        echo "=== Checkpoint Statistics ==="
        echo ""
        
        # Count by type
        dila_count=$(find "$DATA_DIR" -name "*.tar.gz.checkpoint" 2>/dev/null | wc -l)
        csv_count=$(find "$DATA_DIR" -name "*.csv.checkpoint" 2>/dev/null | wc -l)
        json_count=$(find "$DATA_DIR" -name "*.json.checkpoint" 2>/dev/null | wc -l)
        total_count=$(find "$PROJECT_DIR" -name "*.checkpoint" 2>/dev/null | wc -l)
        
        echo "Total checkpoints: $total_count"
        echo "  - DILA archives (.tar.gz): $dila_count"
        echo "  - Data.gouv files (.csv): $csv_count"
        echo "  - Directories/Sheets (.json): $json_count"
        echo ""
        
        if [ "$total_count" -gt 0 ]; then
            echo "Recent checkpoints:"
            find "$PROJECT_DIR" -name "*.checkpoint" -exec ls -lh {} \; 2>/dev/null | tail -5
        fi
        ;;
    
    *)
        echo "Usage: $0 {list|view|delete|clean|stats} [file]"
        echo ""
        echo "Commands:"
        echo "  list          - List all checkpoint files"
        echo "  view <file>   - View checkpoint details"
        echo "  delete <file> - Delete a specific checkpoint file"
        echo "  clean         - Delete all checkpoint files"
        echo "  stats         - Show statistics about checkpoints"
        exit 1
        ;;
esac
