from app import run_qid_expiry_monitoring


if __name__ == "__main__":

    print("")
    print("=========================================================")
    print("STARTING SCHEDULED QID MONITORING")
    print("=========================================================")

    try:

        results = run_qid_expiry_monitoring()

        print("")
        print("=========================================================")
        print("SCHEDULED QID MONITORING FINISHED")
        print("=========================================================")

        print("Results:", results)

        print("=========================================================")

    except Exception as e:

        print("")
        print("=========================================================")
        print("SCHEDULED QID MONITORING FAILED")
        print("=========================================================")

        print(
            "Error Type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e)
        )

        print("=========================================================")

        raise