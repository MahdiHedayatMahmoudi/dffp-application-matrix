# 📊 DFFP Application Matrix

A Streamlit-based tool for extracting, analyzing, and comparing scientific use cases through a **Data Fitness-for-Purpose (DFFP)** framework.

This application enables **cross-paper intelligence**, helping researchers evaluate how well datasets support different applications and identify trade-offs, risks, and reuse potential.

---

## 🚀 Key Features

* 📄 **Multi-paper ingestion** (PDF, TXT, MD)
* 🤖 **LLM-powered structured extraction** into rich Pydantic schemas
* 🎯 **Fitness-for-purpose classification**
* ⚠️ **Risk, uncertainty, and limitation analysis**
* 🧠 **Cross-paper DFFP reasoning pipeline**
* 📊 **Application–data fitness matrix generation**
* 🌐 **Interactive HTML reports**
* 📥 Export to **JSON, CSV, and HTML**

---

## 🧠 Concept: Data Fitness-for-Purpose (DFFP)

This tool implements a **DFFP approach**, which evaluates:

* Whether a dataset is suitable for a given application
* Under what conditions it performs well or fails
* Trade-offs between different use cases
* Risks of misuse and uncertainty propagation

The result is a **cross-paper application matrix** that reveals hidden tensions and synergies between scientific studies.

---

## 🏗️ Project Structure

```bash
dffp-application-matrix/
│
├── app_ui.py                 # Streamlit frontend
├── dffp_pipeline.py          # Cross-paper reasoning engine
├── models.py                 # Pydantic data models
├── utils.py                  # Extraction & HTML utilities
├── interactive_template.html # Interactive report template
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

## ⚙️ Installation

```bash
git clone https://github.com/YOUR_USERNAME/dffp-application-matrix.git
cd dffp-application-matrix

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

---

## 🔑 Environment Setup

Create a `.env` file in the root directory:

```env
OPENAI_API_KEY=your_api_key_here
```

---

## ▶️ Run the Application

```bash
streamlit run app_ui.py
```

Then open the local URL shown in your terminal (usually http://localhost:8501).

---

## 🧪 How It Works

### 1. Upload Papers

Upload multiple documents (PDF, TXT, or Markdown).

### 2. Structured Extraction

The system uses an LLM to extract:

* Application profiles
* Data requirements
* Processing pipelines
* Validation approaches
* Risks and limitations

### 3. Fitness Evaluation

Each use case is enriched with:

* Fitness classification
* Evidence strength
* Operational readiness
* Transferability risk

### 4. Cross-Paper DFFP Analysis

The pipeline:

* Aligns use cases across papers
* Detects shared datasets
* Identifies tensions and trade-offs
* Builds a **fitness matrix**
* Generates an explanatory narrative

---

## 📊 Outputs

* 📦 **Structured JSON** (full extraction)
* 📋 **CSV table** (flattened view)
* 🌐 **HTML reports**:

  * Styled data tables
  * Interactive DFFP matrix

---

## 🔍 Example Use Cases

* Compare multiple studies using the same dataset
* Evaluate dataset reuse potential
* Identify risks in transferring models across contexts
* Support decision-making in agricultural or environmental data systems

---

## 🛠️ Tech Stack

* [Streamlit](https://streamlit.io/) – UI
* [OpenAI API](https://platform.openai.com/) – LLM extraction & reasoning
* [Pydantic](https://docs.pydantic.dev/) – structured schemas
* [Pandas](https://pandas.pydata.org/) – data handling
* [Docling](https://docling-project.github.io/docling/) – PDF parsing

---

## 🔐 Security Notes

* Do **not** commit your `.env` file
* Keep your API keys private
* Use `.gitignore` to exclude sensitive files

---

## 🌱 Context

This tool was developed in the context of **FAIRagro**, but is designed as a **general-purpose framework** for cross-paper data fitness analysis.

---

## 🚀 Future Improvements

* Async processing for faster multi-paper analysis
* Improved dataset detection across studies
* Advanced visualization dashboards
* Integration with knowledge graphs
* Evaluation benchmarks for DFFP outputs

---

## 📜 License

MIT License

---

## 🤝 Contributing

Contributions, ideas, and feedback are welcome!

Feel free to open an issue or submit a pull request.

---

## ⭐ Acknowledgment

If you use this tool in research, please consider citing or referencing the repository.
