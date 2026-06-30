# 🍰 ALX Desserts — Web-Based POS & Inventory Management System

A web-based order and inventory management system built for **ALX Desserts**, a small dessert business operating through Facebook and a physical stall at Banchetto Malolos Convention. Built with Python Flask as a Software Engineering 1 project at Mapúa University.

---

## 📌 Overview

ALX Desserts previously managed orders and inventory manually through chat messages and walk-in transactions. This system centralizes all business operations into a single admin dashboard — covering product management, order recording, payment processing, and sales reporting.

⚠️ Demo Notice: The login credentials provided are for a dummy account used for demonstration purposes only. All data shown is sample data and does not reflect real transactions. 

Username: admin
Password: admin123

---

## ✨ Features

- **Admin Authentication** — Secure login with session management
- **Point of Sale (POS)** — Product grid with real-time cart, quantity controls, and dynamic order summary
- **GCash QR Payment** — QR code popup for cashless transactions alongside cash payment
- **Order Receipts** — Printable receipts generated per transaction with order ID, items, and total
- **Inventory Management** — Add, edit, and delete products with image upload support (PNG/JPG up to 5MB)
- **Sales Reports** — Gross sales, net profit, total orders, and detailed transaction log
- **CSV Export** — Download full sales records as a CSV file
- **Void Transactions** — Remove erroneous orders directly from the sales report page
- **CSV-Based Storage** — Lightweight file-based data persistence (no database required)

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| Frontend | HTML, CSS, JavaScript, Tailwind CSS (CDN) |
| Storage | CSV files |
| Payment | GCash QR integration |
| Dev Environment | PyCharm |

---

## 📂 Project Structure

```
ALX-Desserts/
├── app.py                  # Main Flask application
├── requirements.txt        # Python dependencies
├── data/
│   ├── inventory.csv       # Product records
│   ├── orders.csv          # Order history
│   └── sales.csv          # Sales summary
├── static/
│   ├── css/               # Stylesheets
│   ├── js/                # Scripts
│   └── images/            # Product images
└── templates/
    ├── login.html
    ├── pos.html
    ├── inventory.html
    ├── reports.html
    └── receipt.html
```

---

## 🚀 Getting Started

### Prerequisites
- Python 3.x
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/moriyamao/ALX-Desserts.git
cd ALX-Desserts

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

The app will be available at `http://localhost:5000`.

---

## 👥 Team

**Group 10 — Mapúa University, CSS151P Software Engineering 1**

| Name | Role |
|---|---|
| Morish Alfonso Macayan | Team Lead · Full-Stack Developer — Architected and developed the core system including the POS dashboard, inventory management module, GCash QR payment integration, sales reporting, CSV data pipeline, and receipt generation. Led the team throughout the full software engineering lifecycle from proposal to testing. |
| Denrick Ronn Chua | QA Engineer — Authored test scripts for site availability and image upload validation, ensuring system reliability and correct asset rendering across browsers. |
| Kyle Joniel Gomugda | Frontend Developer — Designed and validated the POS ordering interface, overseeing product selection, cart behavior, and quantity controls. |
| Aaron John Gonzales | Systems Analyst · QA — Documented the Context DFD and GCash payment flow, and authored test scripts verifying the payment integration and dashboard loading. |
| Jethro Lee Lualhati | QA Lead — Authored test scripts covering admin authentication, inventory management, order processing, and transaction recording across the full system. |

**Adviser:** Prof. Elcid A. Serrano

---

## 📄 License

This project was developed for academic purposes under Mapúa University.
