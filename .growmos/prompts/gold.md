You are building the gold set used to score the extraction pipeline. Read the document carefully and produce the reference answer: the entities that are genuinely central to what this document is about (with their types) and the relations between them as (source, target) pairs. Use the current extraction below only as a starting point — add what it missed, remove what is not actually stated or is merely incidental, and correct types. Precision matters more than recall: do not include an entity you cannot point to in the text.

<document source="{source_ref}">
{text}
</document>

<current_extraction>
{extraction}
</current_extraction>

Allowed entity types: {entity_types}
