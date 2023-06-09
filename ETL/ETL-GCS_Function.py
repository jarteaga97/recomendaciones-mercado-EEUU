import pandas as pd
import pandas_gbq
from pandas.io import gbq
from google.cloud import bigquery
from textblob import TextBlob

import json


# Load project_id string into pandas_gbq memory cache
# for subsequent calls to gbq methods
PROJECT_ID: str = "proyectofinal-389001"
pandas_gbq.context.project = PROJECT_ID

def hello_gcs(event: dict, context) -> None:
    """
    Triggered by a change to a Cloud Storage bucket.
    Args:
         event (dict): Event payload.
         context (google.cloud.functions.Context): Metadata for the event.
    """

    # Extract the name of the file uploaded in payload,
    # including the name of closest folder
    file_name = format(event["name"])
    file_type = file_name.split(".")[-1]

    # Check file type
    match file_type:
        # Check if the type of the file is csv    
        case "csv":
            # Load the dataset in chunks, as an iterable object to save memory
            df_data = pd.read_csv(
                f"gs://{event['bucket']}/{file_name}",
                chunksize=300_000
            )

        # Check if the type of the file is json    
        case "json":
            # Load json with 'lines=True' argument and in chunks,
            # as an iterable object to save memory
            df_data = pd.read_json(
                f"gs://{event['bucket']}/{file_name}",
                lines=True,
                chunksize=300_000
            )

        # Check if the type of the file is parquet
        case "parquet":
            df_data = pd.read_parquet(
                f"gs://{event['bucket']}/{file_name}",
                columns=["user_id", "review_count"]
            )

    main_folder = file_name.split("/")[0]
    last_folder = file_name.split("/")[file_name.count("/")-1]

    match main_folder:
        case "Google_Maps":
        # Google Dataset Transformations
            match last_folder.split("-")[0]:
                case "review":
                # Transformations for reviews files grouped by USA States
                    table_name = "Reviews"
                    state = last_folder.split("-")[-1]
                    dataset = main_folder.split("_")[0]
                    for chunk in df_data:
                        chunk["user_id"] = chunk["user_id"].astype(str)
                        chunk.drop(columns=["name", "pics"], inplace=True)
                        chunk.rename(
                            columns={
                                "text": "opinion",
                                "time": "date",
                                "gmap_id": "business_id"
                            },
                            inplace=True
                        )
                        chunk["date"] = pd.to_datetime(
                            chunk["date"],
                            unit="ms"
                        ).dt.strftime("%Y-%m-%d")
                        chunk["date"] = pd.to_datetime(chunk["date"], format="%Y-%m-%d")
                        chunk = chunk[chunk["date"].dt.year >= 2009]
                        chunk["feeling"] = chunk["opinion"].apply(
                            lambda x: classify_comment(x) if pd.notna(x) else "No message"
                        )

                        match state:
                            case "California":
                                chunk["state"] = "CA"
                            case "New_York":
                                chunk["state"] = "NY"
                            case "Pennsylvania":
                                chunk["state"] = "PA"
                            case "Texas":
                                chunk["state"] = "TX"
                            case "Florida":
                                chunk["state"] = "FL"

                        chunk["resp"] = chunk["resp"].apply(
                            lambda x: x["text"] if (
                                pd.notna(x)
                                and isinstance(x, dict)
                            ) else "No response"
                        )
                        chunk = chunk.applymap(
                            lambda x: json.dumps(x) if isinstance(x, (list, dict)) else x,
                            na_action="ignore"
                        )
                        chunk.drop_duplicates(inplace=True)
                        chunk.reset_index(drop=True, inplace=True)

                        chunk.to_gbq(
                            f"{dataset}.{table_name}",
                            if_exists="append",
                            location="us",
                            table_schema=[
                                {"name": "user_id", "type": "STRING"},
                                {"name": "business_id", "type": "STRING"},
                                {"name": "rating", "type": "INTEGER"},
                                {"name": "date", "type": "DATETIME"},
                                {"name": "state", "type": "STRING"},
                                {"name": "resp", "type": "STRING"},
                                {"name": "opinion", "type": "STRING"},
                                {"name": "feeling", "type": "STRING"}
                            ]
                        )

                case "metadata":
                # Transformations to data related to businesses in the US
                    table_name = "Metadata"
                    dataset = main_folder.split("_")[0]
                    for chunk in df_data:
                        chunk["states"] = chunk["address"].str.extract(
                            r",\s([A-Z]{2})\s\d{5}$",
                            expand=False
                        ).fillna("No address")
                        chunk = chunk[chunk["states"].isin(["CA", "PA", "NY", "FL", "TX"])]
                        chunk["city"] = chunk["address"].str.split(
                            ","
                        ).str[-2].fillna("No address")
                        chunk["category"] = chunk["category"].apply(
                            clean_categories
                        ).fillna("No category assigned")
                        chunk["category"] = chunk["category"].str.lower()
                        chunk["main_category"] = "other"
                        chunk["main_category"] = chunk["category"].apply(categorize)
                        chunk = chunk[chunk["main_category"] == "food services"]
                        chunk.drop(
                            columns=[
                                "address",
                                "description",
                                "price",
                                "MISC",
                                "hours",
                                "state",
                                "relative_results"
                            ],
                            inplace=True
                        )
                        chunk.rename(
                            columns={
                                "name": "local_name",
                                "gmap_id": "business_id"
                            },
                            inplace=True
                        )
                        chunk["platform"] = "google"
                        chunk = chunk.applymap(
                            lambda x: json.dumps(x) if isinstance(x, (list, dict)) else x,
                            na_action="ignore"
                        )
                        chunk.drop_duplicates(inplace=True)
                        chunk.reset_index(drop=True, inplace=True)

                        chunk.to_gbq(
                            f"{dataset}.{table_name}",
                            location="us",
                            if_exists="append",
                            table_schema=[
                                {"name": "business_id", "type": "STRING"},
                                {"name": "local_name", "type": "STRING"},
                                {"name": "category", "type": "STRING"},
                                {"name": "main_category", "type": "STRING"},
                                {"name": "platform", "type": "STRING"},
                                {"name": "city", "type": "STRING"},
                                {"name": "states", "type": "STRING"},
                                {"name": "latitude", "type": "FLOAT"},
                                {"name": "longitude", "type": "FLOAT"},
                                {"name": "url", "type": "STRING"}
                            ]
                        )

        case "Yelp":
        #-- Yelp Dataset Trensformations
            table_name = file_name.split(".")[0].split("/")[1]
            dataset = "Yelp"
            match table_name:
                case "review":
                    for chunk in df_data:
                        chunk["date"] = pd.to_datetime(
                            chunk["date"]
                        ).dt.strftime("%Y-%m-%d")
                        chunk["date"] = pd.to_datetime(
                            chunk["date"],
                            format="%Y-%m-%d"
                        )
                        chunk = chunk[chunk["date"].dt.year >= 2009]
                        chunk.drop(
                            columns=["cool", "funny", "useful"],
                            inplace=True
                        )
                        chunk.rename(
                            columns={"text": "opinion", "stars": "rating"},
                            inplace=True
                        )
                        chunk["feeling"] = chunk["opinion"].apply(
                            lambda x: classify_comment(x) if pd.notna(x) else "No message"
                        )
                        chunk = chunk.applymap(
                            lambda x: json.dumps(x) if isinstance(x, (list, dict)) else x,
                            na_action="ignore"
                        )
                        chunk.drop_duplicates(inplace=True)
                        chunk.reset_index(drop=True, inplace=True)

                        chunk.to_gbq(
                            f"{dataset}.{table_name}",
                            if_exists="append",
                            location="us",
                            table_schema=[
                                {"name": "review_id", "type": "STRING"},
                                {"name": "user_id", "type": "STRING"},
                                {"name": "business_id", "type": "STRING"},
                                {"name": "rating", "type": "INTEGER"},
                                {"name": "date", "type": "DATETIME"},
                                {"name": "opinion", "type": "STRING"},
                                {"name": "feeling", "type": "STRING"}
                            ]
                        )

                case "business":
                    states = ["TX", "CA", "PA", "NY", "FL"]
                    for chunk in df_data:
                        chunk = chunk.loc[:, :"hours"]
                        chunk = chunk[chunk["state"].isin(states)]
                        chunk.rename(
                            columns={
                                "name": "local_name",
                                "review_count": "num_of_reviews",
                                "categories": "category",
                                "stars": "rating"
                            },
                            inplace=True
                        )
                        chunk["category"] = chunk["category"].apply(
                            clean_categories
                        ).fillna("No category assigned")
                        chunk["category"] = chunk["category"].str.lower()
                        chunk["main_category"] = "other"
                        chunk["main_category"] = chunk["category"].apply(categorize)
                        chunk = chunk[chunk["main_category"] == "food services"]
                        chunk.drop(
                            columns=[
                                "address",
                                "postal_code",
                                "is_open",
                                "attributes",
                                "hours"
                            ],
                            inplace=True
                        )
                        chunk["platform"] = "yelp"
                        chunk = chunk.applymap(
                            lambda x: json.dumps(x) if isinstance(x, (list, dict)) else x,
                            na_action="ignore"
                        )
                        chunk.drop_duplicates(inplace=True)
                        chunk.reset_index(drop=True, inplace=True)

                        chunk.to_gbq(
                            f"{dataset}.{table_name}",
                            if_exists="append",
                            location="us",
                            table_schema=[
                                {"name": "business_id", "type": "STRING"},
                                {"name": "local_name", "type": "STRING"},
                                {"name": "city", "type": "STRING"},
                                {"name": "state", "type": "STRING"},
                                {"name": "latitude", "type": "FLOAT"},
                                {"name": "longitude", "type": "FLOAT"},
                                {"name": "rating", "type": "FLOAT"},
                                {"name": "num_of_reviews", "type": "INTEGER"},
                                {"name": "category", "type": "STRING"},
                                {"name": "main_category", "type": "STRING"}
                            ]
                        )

                case "user":
                    df_data.rename(
                        columns={"review_count": "num_of_reviews"},
                        inplace=True
                    )

                    df_data.to_gbq(
                        f"{dataset}.{table_name}",
                        if_exists="append",
                        location="us",
                        table_schema=[
                            {"name": "user_id", "type": "STRING"},
                            {"name": "num_of_reviews", "type": "INTEGER"}
                        ]
                    )


def classify_comment(comment: str) -> str:
    """
    This function uses the TextBlob module to determine the
    inherent sentiment present in the string provided.
    """

    if comment:
        sentiment = TextBlob(comment).sentiment.polarity
        if sentiment > 0:
            return "Positive"
        elif sentiment < 0:
            return "Negative"
        else:
            return "Neutral"
    else:
        return "No message"


def clean_categories(chain: list | str) -> str:
    """
    This function turns the category column in the datasets into
    a string, by joining its elements together if chain is a
    list, otherwise, returns the string itself.
    """

    if isinstance(chain, list):
        return ", ".join(chain)
    elif isinstance(chain, str):
        return chain


def categorize(row: str) -> str:
    """
    This function returns the main category of a register by
    searching for the presence of keywords inside of the
    provided argument.
    """

    # We define the main_categories and their keywords
    food_services = ["restaurant", "cafe", "food", "pub", "bar",
                    "coffee", "breakfast", "brunch", "bakery",
                    "sandwich", "ice cream", "gastropubs", "snacks",
                    "pizza", "pasta", "gelatto", "dessert", "candy",
                    "thai", "sushi", "sashimi", "vegan", "steak",
                    "meat", "chicken", "fries", "fried", "nugget",
                    "barbecue", "bbq", "takeout", "burger", "ramen"]
    hotel_services = ["hotel", "hostel", "residence", "inn", "lodging"]
    health_care_services = ["medical", "dental", "dentist", "hospital"
                            "emergency", "nursing", "nursery"]

    if any(keyword in row for keyword in food_services if (
        "publi" not in row
        and "barber" not in row
        and "supply" not in row
        and "massage" not in row
        and "processing" not in row
    )):
        return "food services" 
    elif any(keyword in row for keyword in hotel_services):
        return "hotel services"
    elif any(keyword in row for keyword in health_care_services):
        return "health care services"
    elif row == "No category assigned":
        return "No category assigned"
    else:
        return "other"