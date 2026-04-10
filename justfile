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

