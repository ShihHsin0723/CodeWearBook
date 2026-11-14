import matplotlib.pyplot as plt
import os
from datetime import datetime
from llm import call_llm
import json
import re
from pathlib import Path
from uuid import uuid4
import pandas as pd
from pandas.api.types import is_numeric_dtype, is_bool_dtype


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


ALLOWED_CHART_TYPES = {"bar", "line", "scatter", "histogram"}
VISUALIZATION_OUTPUT_DIR = Path("visualizations")
PARTICIPANT_COLUMNS = ("participant_key", "participant_id", "record_id", "user_id", "respondent_id")
COUNT_COLUMNS = {"count", "participant_count", "individual_count", "number_of_individuals", "num_individuals"}


def validate_visualization_spec(spec, dataframe):
    """Accept chart data only, never Python expressions or output paths."""
    if not isinstance(dataframe, pd.DataFrame) or dataframe.empty:
        raise ValueError("Visualization requires a nonempty dataframe.")
    if not dataframe.columns.is_unique or not all(isinstance(c, str) for c in dataframe.columns):
        raise ValueError("Visualization requires unique string column names.")
    if not isinstance(spec, dict) or set(spec) - {"chart_type", "x", "y", "title"}:
        raise ValueError("Only chart_type, x, y, and title are allowed.")
    chart_type = spec.get("chart_type")
    if not isinstance(chart_type, str) or chart_type not in ALLOWED_CHART_TYPES:
        raise ValueError("Unsupported chart type.")
    title = spec.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Chart title must contain 1–200 characters.")
    for field in ("x", "y"):
        column = spec.get(field)
        if field == "y" and chart_type == "histogram" and column is None:
            continue
        if not isinstance(column, str) or column not in dataframe.columns:
            raise ValueError(f"Unknown dataframe column for {field}.")
    if chart_type == "histogram" and spec.get("y") is not None:
        raise ValueError("A histogram uses x only; y must be null or omitted.")
    numeric_fields = ("x", "y") if chart_type == "scatter" else (("x",) if chart_type == "histogram" else ("y",))
    for field in numeric_fields:
        series = dataframe[spec[field]]
        if not is_numeric_dtype(series) or is_bool_dtype(series):
            raise ValueError(f"Column {spec[field]!r} must be numeric for {chart_type}.")
    fields = [spec["x"]] + ([spec["y"]] if chart_type != "histogram" else [])
    if dataframe[fields].isna().any().any():
        raise ValueError("Selected plotting columns contain missing values; resolve them in the query.")
    for field in numeric_fields:
        series = dataframe[spec[field]]
        if series.isin([float("inf"), -float("inf")]).any():
            raise ValueError("Selected plotting columns must contain finite values.")
    return dict(spec)


def _plot_missingness(spec, dataframe, ax):
    """Render percentage distributions without plotting participant identifiers."""
    x = spec["x"]
    if not re.search(r"percent|percentage|pct", x, re.IGNORECASE):
        raise ValueError("Missingness charts require a percentage column, not raw missing counts.")
    percentages = dataframe[x]
    if not is_numeric_dtype(percentages) or not percentages.between(0, 100).all():
        raise ValueError("Missingness percentages must be numeric values between 0 and 100.")
    if spec["chart_type"] == "histogram":
        entity = next((c for c in PARTICIPANT_COLUMNS if c in dataframe.columns), None)
        if entity is None or dataframe[entity].isna().any() or dataframe[entity].duplicated().any():
            raise ValueError("Missingness histograms require exactly one row per identified participant.")
        ax.hist(percentages, bins=10, range=(0, 100))
    elif spec["chart_type"] == "bar" and spec["y"].lower() in COUNT_COLUMNS:
        counts = dataframe[spec["y"]]
        if (counts < 0).any() or (counts % 1 != 0).any() or percentages.duplicated().any():
            raise ValueError("Missingness bars require unique percentages and nonnegative integer participant counts.")
        ax.bar(percentages, counts)
    else:
        raise ValueError("Use a percentage histogram or a bar chart of precomputed participant counts for missingness.")
    ax.set_xlabel("Missingness (%)")
    ax.set_ylabel("Number of individuals")


def render_visualization(spec, dataframe, *, missingness=False):
    """Render an allowlisted chart using application code and a controlled path."""
    spec = validate_visualization_spec(spec, dataframe)
    fig, ax = plt.subplots()
    try:
        if missingness:
            _plot_missingness(spec, dataframe, ax)
        elif spec["chart_type"] == "histogram":
            ax.hist(dataframe[spec["x"]], bins=10)
            ax.set_xlabel(spec["x"])
            ax.set_ylabel("Frequency")
        else:
            x, y = spec["x"], spec["y"]
            if spec["chart_type"] == "bar":
                # Positions retain separate rows even when category labels repeat.
                positions = range(len(dataframe))
                ax.bar(positions, dataframe[y])
                ax.set_xticks(list(positions), [str(v) for v in dataframe[x]], rotation=45)
            elif spec["chart_type"] == "line":
                ax.plot(dataframe[x], dataframe[y])
            else:
                ax.scatter(dataframe[x], dataframe[y])
            ax.set_xlabel(x)
            ax.set_ylabel(y)
        ax.set_title(spec["title"], parse_math=False)
        fig.tight_layout()
        VISUALIZATION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        filename = VISUALIZATION_OUTPUT_DIR / f"chart_{uuid4().hex}.png"
        fig.savefig(filename)
        print(f"Visualization saved as {filename}")
        return str(filename)
    finally:
        plt.close(fig)


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate visualization fields are not allowed.")
        result[key] = value
    return result


def auto_visualize_data(user_prompt, sql_query, data_frame):
    """Ask the LLM for chart metadata and render it locally without exec()."""
    if not isinstance(data_frame, pd.DataFrame) or data_frame.empty:
        raise ValueError("Visualization requires a nonempty dataframe.")
    schema = [{"name": str(c), "dtype": str(dtype)} for c, dtype in data_frame.dtypes.items()]
    missingness = bool(re.search(r"\b(?:missing\w*|misses|miss)\b", user_prompt, re.IGNORECASE))
    visualization_prompt = f"""Choose ONE chart for the following query result.
User question: {user_prompt}
SQL: {sql_query}
Dataframe columns and types (no row values): {json.dumps(schema)}
Return ONLY a JSON object with chart_type, x, y, and title. No markdown or Python.
chart_type must be bar, line, scatter, or histogram. x and y must be exact column names.
Use a numeric y for bar and line, numeric x and y for scatter, and numeric x with y=null for histogram.
Do not request aggregation, transformations, code, imports, or output filenames.
Use a histogram for distributions of individual numeric observations; use bar for existing counts.
The title must be a nonempty string of at most 200 characters.
For missingness, choose a percentage column (0–100), never individual identifiers as axes.
Use histogram with one row per participant, or bar with precomputed participant counts as y.
"""
    response = call_llm(visualization_prompt)
    if not isinstance(response, str) or len(response) > 10_000:
        raise ValueError("Expected a JSON visualization specification of at most 10,000 characters.")
    try:
        spec = json.loads(response, object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError as exc:
        raise ValueError("The visualization response must be a JSON object, not Python code.") from exc
    return render_visualization(spec, data_frame, missingness=missingness)
