# RAG evaluation

See `docs/evaluation.md`. Quick start (stack running, key in `.env`):

```bash
make eval-rag            # retrieval + answers + lifecycle, golden set
make eval-rag-holdout    # unseen questions, informational
```
Add a case to `golden_set.json` whenever a real question is answered badly: that is how this set should grow.
`golden_holdout.json` must not be used to tune thresholds or weights.
