"""
Checkpoint Manager - Checkpoint management system for data processing.

This module provides functions to save and load the progress state
of data processing, allowing resumption in case of interruption.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from config import get_logger

logger = get_logger(__name__)


class CheckpointManager:
    """Checkpoint manager for tracking processing progress."""

    def __init__(self, source_path: str, checkpoint_suffix: str = ".checkpoint"):
        """
        Initializes the checkpoint manager.

        Args:
            source_path (str): Path of the source file/folder being processed
            checkpoint_suffix (str): Suffix of the checkpoint file (default: ".checkpoint")
        """
        self.source_path = source_path
        self.checkpoint_file = f"{source_path}{checkpoint_suffix}"

    def load(self) -> int:
        """
        Loads the last processed index from the checkpoint.

        Returns:
            int: Index of the last processed item, or -1 if no checkpoint exists
        """
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    last_index = data.get("last_processed_index", -1)
                    timestamp = data.get("timestamp", "unknown")
                    logger.info(
                        f"Resuming from checkpoint: index {last_index + 1} "
                        f"(saved at {timestamp})"
                    )
                    return last_index
            except Exception as e:
                logger.warning(
                    f"Could not load checkpoint from {self.checkpoint_file}: {e}"
                )
        return -1

    def save(self, index: int, metadata: Optional[Dict[str, Any]] = None):
        """
        Saves the current index to the checkpoint.

        Args:
            index (int): Index of the processed item
            metadata (dict, optional): Additional metadata to save
        """
        try:
            data = {
                "last_processed_index": index,
                "timestamp": datetime.now().isoformat(),
            }

            # Add metadata if provided
            if metadata:
                data["metadata"] = metadata

            with open(self.checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            logger.warning(f"Could not save checkpoint to {self.checkpoint_file}: {e}")

    def remove(self):
        """Removes the checkpoint file after complete processing."""
        try:
            if os.path.exists(self.checkpoint_file):
                os.remove(self.checkpoint_file)
                logger.debug(f"Checkpoint file removed: {self.checkpoint_file}")
        except Exception as e:
            logger.warning(
                f"Could not remove checkpoint file {self.checkpoint_file}: {e}"
            )

    def exists(self) -> bool:
        """
        Checks if a checkpoint exists.

        Returns:
            bool: True if the checkpoint exists, False otherwise
        """
        return os.path.exists(self.checkpoint_file)

    def get_info(self) -> Optional[Dict[str, Any]]:
        """
        Retrieves checkpoint information.

        Returns:
            dict or None: Checkpoint information or None if no checkpoint exists
        """
        if not self.exists():
            return None

        try:
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not read checkpoint info: {e}")
            return None
