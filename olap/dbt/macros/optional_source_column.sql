{# True when the raw table has the column. Lets a model read a column that a newer
   OLTP schema adds and still build against an older raw load (parse always says False). #}
{% macro has_source_column(source_name, table_name, column) %}
    {% if not execute %}
        {{ return(false) }}
    {% endif %}
    {% set relation = adapter.get_relation(
        database=source(source_name, table_name).database,
        schema=source(source_name, table_name).schema,
        identifier=source(source_name, table_name).identifier) %}
    {% if relation is none %}
        {{ return(false) }}
    {% endif %}
    {% set names = adapter.get_columns_in_relation(relation) | map(attribute='name') | map('lower') | list %}
    {{ return(column | lower in names) }}
{% endmacro %}
