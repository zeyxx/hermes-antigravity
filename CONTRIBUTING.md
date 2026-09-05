# Contributing to Hermes Antigravity Plugin

Thank you for your interest in contributing to `hermes-antigravity`! This project is an open-source model provider plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

---

## 🛠️ Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/zeyxx/hermes-antigravity.git
   cd hermes-antigravity
   ```

2. **Set up Python environment (3.10+):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install pytest ruff
   ```

3. **Install into your local Hermes instance:**
   ```bash
   mkdir -p ~/.hermes/plugins/model-providers
   ln -s "$(pwd)" ~/.hermes/plugins/model-providers/antigravity
   ```

---

## 🧪 Testing & Linting

Before opening a PR, ensure all tests pass and code style is clean:

```bash
# Lint code
ruff check .

# Run test suite
PYTHONPATH=.:~/.hermes/hermes-agent pytest tests/ -v
```

---

## 🤝 Contribution Guidelines

1. **Keep it focused**: Only features and fixes related to Google Antigravity / Cloud Code Assist inference and Hermes integration.
2. **Zero secrets policy**: Never commit credentials, personal tokens, or API keys. Automated GitHub Push Protection is enabled.
3. **Bilingual documentation**: If modifying documentation or adding user-facing features, please update both `README.md` (EN) and `README.fr.md` (FR).
4. **TDD first**: Add or update unit tests in `tests/` covering any behavioral change.
