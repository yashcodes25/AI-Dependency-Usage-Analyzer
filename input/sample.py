import pandas as pd
import numpy as np
from flask import Flask
import requests
import sqlite3
import matplotlib.pyplot as plt

app = Flask(__name__)

def analyze_data():
    data = pd.DataFrame({
        "marks": np.random.randint(60, 100, 5)
    })

    average = np.mean(data["marks"])

    response = requests.get("https://jsonplaceholder.typicode.com/users")

    conn = sqlite3.connect("student.db")

    plt.plot(data["marks"])
    plt.title("Student Marks")

    return {
        "average": average,
        "api_status": response.status_code
    }

if __name__ == "__main__":
    print(analyze_data())