set shell := ["bash", "-cu"]
set positional-arguments := true

create_blog_entry *args:
    PYTHONPATH=. uv run python -m page_creator.create_blog_entry "$@"

clear_blog_entries *args:
    PYTHONPATH=. uv run python -m page_creator.clear_blog_entries "$@"

remove_unused_documents *args:
    PYTHONPATH=. uv run python -m page_creator.remove_unused_documents "$@"

author_lister *args:
    PYTHONPATH=. uv run python -m metadata_editor.author_lister "$@"

set_publication_date *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_publication_date "$@"

set_collection *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_collection "$@"

set_themes *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_themes "$@"

theme_reventilation *args:
    PYTHONPATH=. uv run python -m metadata_editor.theme_reventilation "$@"

set_html *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_html "$@"

create_theme_categories *args:
    PYTHONPATH=. uv run python -m metadata_editor.create_theme_categories "$@"

reformat_disaron *args:
    PYTHONPATH=. uv run python -m metadata_editor.reformat_disaron "$@"

add_ids_to_pages *args:
    PYTHONPATH=. uv run python -m metadata_editor.add_ids_to_pages "$@"

wrap_chapeau *args:
    PYTHONPATH=. uv run python -m metadata_editor.wrap_chapeau "$@"

disaron_fixer *args:
    PYTHONPATH=. uv run python -m metadata_editor.disaron_fixer "$@"

export_to_csv *args:
    PYTHONPATH=. uv run python -m data_exporter.export_to_csv "$@"

sort_theme_codes_column *args:
    PYTHONPATH=. uv run python -m data_exporter.sort_theme_codes_column "$@"

