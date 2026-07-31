---
# A toolset MEMBER. Frontmatter shape == the singleton example.
name: create_ticket
description: "Create a support ticket"
type: http

# permission: confirm triggers a user-approval popup BEFORE execution.
permission: confirm

# Fake/illustrative endpoint — never executed.
endpoint: "https://api.example.com/tickets"

# POST: arguments become the JSON request body (not the query string).
method: POST

# JMESPath into the (illustrative) JSON response.
response_extract: "id"

input_schema:
  type: object
  properties:
    title:
      type: string
      description: "Short summary of the issue"
    priority:
      type: string
      description: "Ticket priority"
      enum: [low, medium, high]
      default: "medium"
  required: [title]
  additionalProperties: false
---

Create a support ticket with the given title and priority.
Use this when the user asks to file or open a ticket.
