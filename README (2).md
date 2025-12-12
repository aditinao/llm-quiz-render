# 🤖 Gemini Multimodal Quiz Solver

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An autonomous agent designed to solve **sequential, multimodal web challenges** using **Gemini 1.5 Flash**.  
Built to comply with **Gemini Free Tier limits** and the evaluation style used in IITM TDS Project-2.

---

## 🌟 Overview

This project implements a resilient quiz-solving agent capable of handling **text, image, audio, and web-based tasks**.  
It autonomously scrapes, processes, analyzes, and submits answers while respecting rate limits and server constraints.

---

## ✨ Key Features

- **Multimodal Support**
  - Image analysis (`.png`, `.jpg`)
  - Audio transcription (`.wav`, `.opus`)
- **Gemini Free Tier Safe**
  - Optimized for **15 requests per minute**
- **Safety Watchdog**
  - 165-second per-task timeout to avoid server hangs
- **Resilient Architecture**
  - Automatic recovery from Gemini 404 routing / transient API errors
- **Sequential Task Solving**
  - Handles chained questions where each response reveals the next URL

---

## 🧠 Task Logic

The agent dynamically decides the required action based on the question type.

Supported task categories include:

1. **Web Scraping** (HTML / JS-rendered content)  
2. **API Consumption** (custom headers, authenticated requests)  
3. **Data Cleansing** (text, tables, PDFs, API outputs)  
4. **Processing** (OCR, transcription, vision analysis)  
5. **Analysis** (filtering, aggregation, statistical / ML logic)  
6. **Visualization** (charts, narratives, slides when required)

---

## 🚀 Setup Instructions

### 1️⃣ Get API Key
Create a Gemini API key from:  
https://aistudio.google.com/

### 2️⃣ Set Environment Variable
```bash
export GEMINI_API_KEY="your_api_key_here"
```

(Windows PowerShell)
```powershell
setx GEMINI_API_KEY "your_api_key_here"
```

### 3️⃣ Install Dependencies
```bash
pip install -r requirements.txt
```

### 4️⃣ Run the Agent
```bash
uv run initiate.py
```

---

## 📦 Dependencies

```txt
google-generativeai>=0.8.3
requests
beautifulsoup4
python-dotenv
```

---

## 🧪 Evaluation Compatibility

- Designed for **IITM TDS Project-2** evaluations
- Compatible with `/demo` and chained endpoints
- Handles missing-field and partial-response scenarios
- Submits answers autonomously once requirements are met

---

## 📝 License

**MIT License**

Copyright (c) 2025  
**23f2003906@ds.study.iitm.ac.in**

Permission is hereby granted, free of charge, to any person obtaining a copy  
of this software and associated documentation files (the "Software"), to deal  
in the Software without restriction.

---

**Ready for submission 🚀**
