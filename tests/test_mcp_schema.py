import unittest

from nailong.mcp.schema import schema_validator


class MCPJSONSchemaTests(unittest.TestCase):
    def test_properties_named_ref_and_inert_example_data_are_valid(self):
        schema = {'type': 'object', 'properties': {'$ref': {'type': 'string'}},
                  'default': {'$ref': 'https://example.invalid/data'}}
        validator = schema_validator(schema)
        self.assertTrue(validator.is_valid({'$ref': 'ordinary business data'}))

    def test_local_defs_validate_nested_data_and_external_refs_are_rejected(self):
        schema = {'type': 'object', '$defs': {'amount': {'type': 'integer'}},
                  'properties': {'amount': {'$ref': '#/$defs/amount'}}}
        validator = schema_validator(schema)
        self.assertTrue(validator.is_valid({'amount': 3}))
        self.assertFalse(validator.is_valid({'amount': 'wrong'}))
        with self.assertRaises(ValueError):
            schema_validator({'type': 'object', 'properties': {'amount': {'$ref': 'https://example.invalid/schema'}}})
