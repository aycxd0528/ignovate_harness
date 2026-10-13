# GUI controls and workspace navigation refinement

The user supplied the interaction design and authorized continued implementation. Work in the existing dirty checkout without staging or committing unrelated files.

1. Add native GUI model catalog/add/select commands using PreferenceStore. Preserve current provider credentials; selecting a model resets incompatible effort to default atomically. Reject model mutations during queued or active runs. Test persistence, validation, and project isolation with an offline native model.
2. Add read-only metadata for explicitly opened/recent workspace paths, using the existing project session location. No model initialization, history rewrite, or creation of databases while listing other workspaces. Render workspace groups/tree/single-list, collapse, relative update times, manual order/recent order, and archive filters. Keep map scope to the current conversation.
3. Merge model and effort controls: capsule -> continuous effort panel -> central header -> native model menu, with add-model and connection settings. Keep existing shader animation, supported effort choices, serialized saves, and cleanup.
4. Replace separate task mode control with an add menu containing file/folder references, implementation/plan/review/goal modes. Use native bounded goal driver and explicit limits; never start requests until Send. Folder reference lists bounded native directory metadata without reading recursive content.
5. Collapse tool history behind an aggregate summary; show the active tool separately while running. Preserve expansion across updates. Remove user-bubble blue border, align sidebar entries, use orange full access and right-aligned readiness with green glow, and display task durations from one second.
6. Run native tests and offline browser interaction regressions, review the changes independently, fix findings, run full checks, capture light/dark UI screenshots, and refresh idle user preview/desktop.

Visual direction: existing system font and neutral surfaces (#141414, #1e1e1e, #2b2b2b / light equivalents), blue (#397cf6) reserved for selected controls and effort animation, orange (#f58a45) for full access, green (#43c98c) for readiness. Left-align sidebar navigation and nest sessions beneath folder headers; keep one compact composer control row.
