"""Shared constants for the model-facing XML tool-call protocol."""

STRUCTURAL_TAGS = frozenset({'reason', 'name', 'params'})

TOOL_CALL_EXAMPLE = """<tool_call>
  <reason><![CDATA[why you are making this call]]></reason>
  <name>tool_name</name>
  <params>
    <replace_with_param_name><![CDATA[value]]></replace_with_param_name>
  </params>
</tool_call>"""
