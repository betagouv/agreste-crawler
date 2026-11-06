set shell := ["bash", "-cu"]
set positional-arguments := true

create_blog_entry *args:
    PYTHONPATH=. uv run python -m page_creator.old.create_blog_entry "$@"

clear_child_page *args:
    PYTHONPATH=. uv run python -m page_creator.clear_child_page "$@"

remove_unused_documents *args:
    PYTHONPATH=. uv run python -m page_creator.remove_unused_documents "$@"

bugfix_document_size *args:
    PYTHONPATH=. uv run python -m page_creator.old.bugfix_document_size "$@"

author_lister *args:
    PYTHONPATH=. uv run python -m metadata_editor.author_lister "$@"

set_publication_date *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.set_publication_date "$@"

set_collection *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.set_collection "$@"

set_themes *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_themes "$@"

set_source *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_source "$@"

remove_source_from_subtitle *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.remove_source_from_subtitle "$@"

set_reference_years_and_geographic_levels *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_reference_years_and_geographic_levels "$@"

set_nomenclatures *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_nomenclatures "$@"

set_issue_number *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_issue_number "$@"

set_issue_number_from_inventaire *args:
    PYTHONPATH=. uv run python -m metadata_editor.set_issue_number_from_inventaire "$@"

theme_reventilation *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.theme_reventilation "$@"

set_html *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.set_html "$@"

set_text_fields *args:
    PYTHONPATH=. uv run python -m metadata_editor.html.set_text_fields "$@"

create_theme_categories *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.create_theme_categories "$@"

reformat_disaron *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.reformat_disaron "$@"

add_ids_to_pages *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.add_ids_to_pages "$@"

wrap_chapeau *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.wrap_chapeau "$@"

disaron_fixer *args:
    PYTHONPATH=. uv run python -m metadata_editor.old.disaron_fixer "$@"

export_to_csv *args:
    PYTHONPATH=. uv run python -m data_exporter.export_to_csv "$@"

export_publications_to_csv *args:
    PYTHONPATH=. uv run python -m data_exporter.export_publications_to_csv "$@"

sort_theme_codes_column *args:
    PYTHONPATH=. uv run python -m data_exporter.sort_theme_codes_column "$@"

extract_disaron_links *args:
    PYTHONPATH=. uv run python -m metadata_editor.html.extract_disaron_links "$@"

get_raw_text_fields *args:
    PYTHONPATH=. uv run python -m metadata_editor.html.get_raw_text_fields "$@"

extract_modalities *args:
    PYTHONPATH=. uv run python -m metadata_editor.extract_modalities "$@"

hierarchize_modalities *args:
    PYTHONPATH=. uv run python -m metadata_editor.hierarchize_modalities "$@"

clean_text_fields *args:
    PYTHONPATH=. uv run python -m metadata_editor.html.clean_text_fields "$@"

