"""
Unit tests for config validation.

Tests the validate_config() function in pyznap.utils.
"""

import os
import tempfile

import pytest

from pyznap.utils import validate_config


class TestValidateConfig:
    """Test config validation function."""

    def test_valid_config(self):
        """Test that valid config passes validation."""
        config = [
            {
                'name': 'tank/data',
                'hourly': 24,
                'daily': 7,
                'weekly': 4,
                'snap': True,
                'clean': True,
                'dest': ['backup/data'],
                'compress': ['lzop'],
            }
        ]

        errors = validate_config(config)
        assert len(errors) == 0

    def test_negative_snapshot_count(self):
        """Test that negative snapshot counts are rejected."""
        config = [
            {
                'name': 'tank/data',
                'hourly': -5,  # Invalid
                'snap': True,
            }
        ]

        errors = validate_config(config)
        assert len(errors) == 1
        assert 'hourly must be >= 0' in errors[0]

    def test_invalid_snapshot_type(self):
        """Test that non-integer snapshot counts are rejected."""
        config = [
            {
                'name': 'tank/data',
                'hourly': 'abc',  # Invalid
                'snap': True,
            }
        ]

        errors = validate_config(config)
        assert len(errors) == 1
        assert 'hourly must be an integer' in errors[0]

    def test_invalid_boolean_option(self):
        """Test that invalid boolean values are rejected."""
        config = [
            {
                'name': 'tank/data',
                'snap': 'maybe',  # Invalid
                'hourly': 24,
            }
        ]

        errors = validate_config(config)
        assert len(errors) == 1
        assert 'snap must be yes/no' in errors[0]

    def test_mismatched_dest_and_compress(self):
        """Test that dest and compress arrays must have same length."""
        config = [
            {
                'name': 'tank/data',
                'hourly': 24,
                'snap': True,
                'dest': ['backup/data', 'ssh::user@host:backup/data'],
                'compress': ['lzop'],  # Only 1 entry, but dest has 2
            }
        ]

        errors = validate_config(config)
        assert len(errors) == 1
        assert 'dest has 2 entries but compress has 1' in errors[0]

    def test_mismatched_dest_and_dest_keys(self):
        """Test that dest and dest_keys arrays must have same length."""
        # Create temporary key files
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.key') as f1:
            key1 = f1.name
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.key') as f2:
            key2 = f2.name

        try:
            config = [
                {
                    'name': 'tank/data',
                    'hourly': 24,
                    'snap': True,
                    'dest': ['backup/data'],
                    'dest_keys': [key1, key2],  # Too many
                }
            ]

            errors = validate_config(config)
            assert len(errors) == 1
            assert 'dest has 1 entries but dest_keys has 2' in errors[0]
        finally:
            os.unlink(key1)
            os.unlink(key2)

    def test_nonexistent_ssh_key(self):
        """Test that missing SSH key files are detected."""
        config = [{'name': 'tank/data', 'hourly': 24, 'snap': True, 'key': '/nonexistent/path/id_rsa'}]

        errors = validate_config(config)
        assert len(errors) == 1
        assert 'SSH key file not found' in errors[0]

    def test_nonexistent_dest_key(self):
        """Test that missing dest_keys files are detected."""
        config = [
            {
                'name': 'tank/data',
                'hourly': 24,
                'snap': True,
                'dest': ['ssh::user@host:backup/data'],
                'dest_keys': ['/nonexistent/path/id_rsa'],
            }
        ]

        errors = validate_config(config)
        assert len(errors) == 1
        assert 'dest_keys[0] file not found' in errors[0]

    def test_existing_ssh_key(self):
        """Test that existing SSH key files pass validation."""
        # Create a temporary key file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.key') as f:
            key_path = f.name
            f.write('fake key content')

        try:
            config = [{'name': 'tank/data', 'hourly': 24, 'snap': True, 'key': key_path}]

            errors = validate_config(config)
            assert len(errors) == 0
        finally:
            os.unlink(key_path)

    def test_multiple_errors(self):
        """Test that multiple errors are all reported."""
        config = [
            {
                'name': 'tank/data',
                'hourly': -5,  # Error 1
                'daily': 'abc',  # Error 2
                'snap': 'maybe',  # Error 3
                'dest': ['backup/data'],
                'compress': ['lzop', 'gzip'],  # Error 4
            }
        ]

        errors = validate_config(config)
        assert len(errors) == 4

    def test_valid_max_depth(self):
        """Test that valid max_depth values pass."""
        config = [{'name': 'tank/data', 'hourly': 24, 'max_depth': 5}]

        errors = validate_config(config)
        assert len(errors) == 0

    def test_invalid_max_depth(self):
        """Test that invalid max_depth is rejected."""
        config = [{'name': 'tank/data', 'hourly': 24, 'max_depth': 'invalid'}]

        errors = validate_config(config)
        assert len(errors) == 1
        assert 'max_depth must be an integer' in errors[0]

    def test_empty_config(self):
        """Test that empty config passes validation."""
        config = []
        errors = validate_config(config)
        assert len(errors) == 0

    def test_root_filesystem(self):
        """Test validation for root filesystem (//)."""
        config = [{'name': '', 'hourly': 24, 'snap': True}]

        errors = validate_config(config)
        assert len(errors) == 0


class TestValidateConfigWarnings:
    """Test config validation warnings (logged but not fatal)."""

    def test_dest_without_snap_warning(self, caplog):
        """Test warning when dest is set but snap is disabled."""
        import logging

        caplog.set_level(logging.WARNING)

        config = [
            {
                'name': 'tank/data',
                'hourly': 24,
                'snap': False,  # snap disabled
                'dest': ['backup/data'],  # but dest configured
            }
        ]

        errors = validate_config(config)
        assert len(errors) == 0  # No errors, just warning
        assert 'has dest configured but snap=no' in caplog.text

    def test_snap_without_types_warning(self, caplog):
        """Test warning when snap is enabled but no snapshot types configured."""
        import logging

        caplog.set_level(logging.WARNING)

        config = [
            {
                'name': 'tank/data',
                'snap': True,
                # No snapshot types configured
            }
        ]

        errors = validate_config(config)
        assert len(errors) == 0  # No errors, just warning
        assert 'snap=yes but no snapshot types configured' in caplog.text


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
