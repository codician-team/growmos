Extract a knowledge graph from the document below.

<document source="{source_ref}">
{text}
</document>

Guidelines:
- Extract only entities that are central to what this document is about — skip incidental mentions.
- For each entity, write a one-sentence description grounded in this document. These descriptions are used later to disambiguate entities with similar names.
- Predicates should be short verb phrases ("commanded", "launched from", "part of", "depends on").
- Every relation must connect two entities you extracted.
- Allowed entity types: {entity_types}
- Predicate vocabulary already in use (reuse when it fits, coin new short verb phrases when it doesn't): {predicate_hints}
- Do not invent facts that are not stated in the document.
