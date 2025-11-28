"""
pyznap.output
~~~~~~~~~~~~~~

JSON output handler for pyznap commands.

:copyright: (c) 2018-2019 by Yannick Boetzel.
:license: GPLv3, see LICENSE for more details.
"""

import json
import sys
from datetime import datetime


class OutputHandler:
    """Handles structured output for pyznap commands in JSON format."""

    def __init__(self, output_format='log', command=None):
        """Initialize the output handler.

        Parameters
        ----------
        output_format : str
            Output format: 'log', 'json', or 'jsonl'
        command : str
            Command name (e.g., 'take', 'send', 'clean')
        """
        self.output_format = output_format
        self.command = command
        self.operations = []
        self.start_time = datetime.now()
        self.stats = {}
        self.status = 'success'

    def add_operation(self, operation):
        """Add an operation to the list.

        Parameters
        ----------
        operation : dict
            Operation details to add
        """
        if self.output_format == 'log':
            # No collection needed for log format
            return

        # Add timestamp if not present
        if 'timestamp' not in operation:
            operation['timestamp'] = datetime.now().isoformat()

        self.operations.append(operation)

        # For jsonl format, output immediately
        if self.output_format == 'jsonl':
            print(json.dumps(operation))
            sys.stdout.flush()

    def set_stats(self, stats):
        """Set statistics for the operation.

        Parameters
        ----------
        stats : dict
            Statistics to include in output
        """
        self.stats = stats

    def set_status(self, status):
        """Set overall status.

        Parameters
        ----------
        status : str
            Status: 'success', 'error', or 'partial'
        """
        self.status = status

    def finalize(self):
        """Output final results for json format."""
        if self.output_format == 'json':
            result = {
                'command': self.command,
                'timestamp': self.start_time.isoformat(),
                'duration_seconds': (datetime.now() - self.start_time).total_seconds(),
                'operations': self.operations,
                'status': self.status,
            }

            # Add stats if available
            if self.stats:
                result['stats'] = self.stats

            print(json.dumps(result, indent=2))
            sys.stdout.flush()
        elif self.output_format == 'jsonl':
            # For jsonl, we might want to output a summary at the end
            summary = {
                'type': 'summary',
                'command': self.command,
                'timestamp': datetime.now().isoformat(),
                'duration_seconds': (datetime.now() - self.start_time).total_seconds(),
                'total_operations': len(self.operations),
                'status': self.status,
            }

            if self.stats:
                summary['stats'] = self.stats

            print(json.dumps(summary))
            sys.stdout.flush()
