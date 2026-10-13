# Map live runs, links and recorded file diffs

User requested a cause-based fix to disappearing map follow-ups, live connectors, file change review, and persistent expanded-button highlights. Continue the authorized GUI refinement in the existing checkout without committing unrelated files.

1. Native write/edit results record committed unified patch, full added/deleted counts and explicit bounded-preview truncation. AgentService persists redacted diff metadata in tool-end events; GUI artifacts retain recorded operations instead of reading the current working tree as history.
2. Map runs expose native request identity and immutable map origin. Render queued/running cards, stream text and tool status in the map; reserve draft position/camera and migrate to the committed node position. Reload must preserve an in-flight card without replaying the request.
3. Update SVG links and dependent draft positions on pointer movement, preserving editable draft text and focus. Expand originating collapsed paths when placing drafts. Persist camera adjustments and accept the UI's existing 4x zoom range.
4. Provide file counts, colored unified diff lines and a review dialog with file navigation, keeping tool history collapsed by default. Retain keyboard focus outlines while removing persistent expanded-state button backgrounds.
5. Run failing-first native and delayed offline browser regressions, existing UI/map/lifecycle checks, independent read-only review, Python full suite, and packaging checks. Refresh idle actual preview/desktop and capture screenshots.
