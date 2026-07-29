# Contributing

Thanks for your interest in improving the **AI Recruiting Sourcing Pipeline**. Contributions of all
sizes are welcome — bug reports, documentation fixes, workflow improvements, and new ideas.

## Ways to contribute

- **Report a bug or request a feature** by opening an [issue](https://github.com/shravangithub/ai-recruiting-sourcing-pipeline/issues).
  Include steps to reproduce, what you expected, and what happened.
- **Improve the docs** — the demo page lives in `docs/`, and the README covers setup and usage.
- **Improve the workflow** — the importable n8n definition is `ai-recruiting-pipeline.template.json`.

## Development workflow

1. Fork the repository and create a branch: `git checkout -b feature/short-description`.
2. Make your change. If you edit the n8n workflow, export the updated flow and replace
   `ai-recruiting-pipeline.template.json` so others can import your version.
3. Test against your own n8n instance with your own credentials — do **not** commit any API keys,
   tokens, sheet IDs, or other secrets.
4. Commit with a clear message and open a pull request describing the change and why.

## Guidelines

- Keep secrets out of commits. Credentials belong in n8n's credential store, never in the template
  JSON or the repo.
- Prefer small, focused pull requests — they are easier to review and merge.
- Be respectful and constructive in issues and reviews.

## License

By contributing, you agree that your contributions are licensed under the project's
[MIT License](./LICENSE).

