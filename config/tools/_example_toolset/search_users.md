---
# A toolset MEMBER. Its callable name is the unit name joined to this `name`
# by a double-underscore. Frontmatter shape == the singleton example.
name: search_users
description: "Search the user directory by free-text query"
type: http

# permission: confirm triggers a user-approval popup BEFORE execution.
permission: confirm

# Fake/illustrative endpoint — never executed.
endpoint: "https://api.example.com/users/search"

# GET: parameters become the query string.
method: GET

# JMESPath into the (illustrative) JSON response. `[*]` projects a field across a
# list — supported by JMESPath (the old dotted-path parser could not do this).
response_extract: "results[*].id"

parameters:
  - name: query
    type: string
    description: "Free-text search term, e.g. a name or email fragment"
    required: true
---

Search the user directory and return matching user IDs.
Use this when the user wants to look someone up by name or email.
