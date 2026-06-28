import boto3
from botocore.exceptions import ClientError
import json

def get_api_keys():

    secret_name = "llm/api_keys"
    region_name = "us-west-1"

    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        raise e

    secret = get_secret_value_response['SecretString']
    secret_dict = json.loads(secret)
    return secret_dict

if __name__ == "__main__":
    try:
        api_keys = get_api_keys()
        
        anthropic_api_key = api_keys.get('anthropic_api_key')
        gemini_api_key = api_keys.get('gemini_api_key')
        openai_api_key = api_keys.get('openai_api_key')
        together_api_key = api_keys.get('together_api_key')

        print("Successfully retrieved secrets!")
        print(f"Available keys: {list(api_keys.keys())}")
        
    except Exception as e:
        print(f"Error: {e}")   

