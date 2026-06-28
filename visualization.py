import matplotlib.pyplot as plt
import os
from datetime import datetime
from llm import call_llm
import builtins
import pandas as pd
import numpy as np


def visualize_data(dataframe):
    """
    Provide visualization options for the DataFrame.
    """
    print("Choose a visualization option:")
    print("1. Bar Chart")
    print("2. Line Chart")
    print("3. Scatter Plot")
    print("4. Histogram")
    print("5. Exit Visualization")

    while True:
        os.makedirs("visualizations", exist_ok=True)
        choice = input("Enter your choice (1-5): ")
        if choice == "1":
            x_col = input("Enter the column name for the X-axis: ")
            y_col = input("Enter the column name for the Y-axis: ")
            print(dataframe[[x_col, y_col]].dropna().head())

            plt.clf()
            plt.close()
            dataframe.plot.bar(x=x_col, y=y_col, legend=False)
            plt.title(f"{y_col} vs {x_col}")

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = os.path.join(
                "visualizations", f"{y_col}_vs_{x_col}_bar_chart_{timestamp}.png")
            plt.savefig(filename)
            print(f"Bar chart saved as '{filename}'.")
            input("Press Enter to continue...")
            break

        elif choice == "2":
            x_col = input("Enter the column name for the X-axis: ")
            y_col = input("Enter the column name for the Y-axis: ")
            print(dataframe[[x_col, y_col]].dropna().head())

            plt.clf()
            plt.close()
            dataframe.plot(x=x_col, y=y_col, legend=False)
            plt.title(f"{y_col} vs {x_col}")

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = os.path.join(
                "visualizations", f"{y_col}_vs_{x_col}_line_chart_{timestamp}.png")
            plt.savefig(filename)
            print(f"Line chart saved as '{filename}'.")

            input("Press Enter to continue...")
            break

        elif choice == "3":
            x_col = input("Enter the column name for the X-axis: ")
            y_col = input("Enter the column name for the Y-axis: ")
            print(dataframe[[x_col, y_col]].dropna().head())

            plt.clf()
            plt.close()
            dataframe.plot.scatter(x=x_col, y=y_col)
            plt.title(f"{y_col} vs {x_col}")

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = os.path.join(
                "visualizations", f"{y_col}_vs_{x_col}_scatter_plot_{timestamp}.png")
            plt.savefig(filename)
            print(f"Scatter plot saved as '{filename}'.")

            input("Press Enter to continue...")
            break

        elif choice == "4":
            col = input("Enter the column name for the histogram: ")
            bins = int(input("Enter the number of bins: "))
            print(dataframe[[col]].dropna().head())

            plt.clf()
            plt.close()
            dataframe[col].dropna().plot.hist(bins=bins)
            plt.title(f"Histogram of {col}")
            plt.xlabel(col)
            plt.ylabel("Frequency")

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = os.path.join(
                "visualizations", f"{col}_histogram_{timestamp}.png")
            plt.savefig(filename)
            print(f"Histogram saved as '{filename}'.")

            input("Press Enter to continue...")
            break

        elif choice == "5":
            print("Exiting visualization.")
            break

        else:
            print("Invalid choice. Please try again.")
    return


def auto_visualize_data(user_prompt, sql_query, data_frame):
    """
    Generate data frame visualization automatically using LLM.
    """
    visualization_prompt = f"""
    Based on the user prompt: {user_prompt}
    There is this SQL query: {sql_query}
    It returned a pandas DataFrame named `dataframe`.

    Write EXECUTABLE Python that:
    - Uses ONLY matplotlib or pandas.DataFrame.plot (which uses matplotlib) to create ONE appropriate chart
      (bar, line, scatter, or histogram) chosen from the data and prompt.
    - Use histogram when the prompt asks for distribution.
    - Automatically picks sensible x and y columns if needed.
    - Always start with:
    plt.clf()
    plt.close()
    - Always set a title.
    - Always save the figure to a PNG in the ./visualizations directory using plt.savefig(filename).
    - For missingness visualizations, do NOT plot individual records; use a plot with x-axis = percent of missingness and y-axis = number of individuals.
    - The filename must be a simple lowercase string with underscores and end in ".png".
    - Do NOT import anything. Do NOT read/write files besides plt.savefig.
    - Do NOT show the plot (no plt.show()).
    - Always print out "Visualization saved as <filename>."
    - Assume `dataframe` is available.

    ONLY output Python code. No comments or explanations. DO NOT wrap in markdown code blocks.
    """
    visualization_code = call_llm(visualization_prompt)

    if not isinstance(visualization_code, str):
        raise ValueError("LLM did not return code as a string.")

    print(visualization_code)

    # Create execution environment
    env = {
        "dataframe": data_frame,
        "plt": plt,
        "pd": pd,
        "np": np,
        "__builtins__": builtins.__dict__,
    }

    try:
        exec(visualization_code, env)
        print("Execute completed!")
    except Exception as e:
        print(f"Error executing visualization code: {e}")
        raise
