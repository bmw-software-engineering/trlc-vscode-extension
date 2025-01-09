# apply trlc parser and errors and any other needed module,
# just as server.py from original extension
from trlc import parser, errors

def perform_sanity_checks(files):
    # Placeholder to immplement logic to 
    # parse files and perform the sanity checks
    results = []
    for file in files:
        # do parsing using existing TRLC modules
        # like parser, errors, or lexer
        # depending on needs
        print("finished")
    return results

def main():

    # pass worskpace as argument to parse, probably.
    # filter files for .trlc
    trlc_files = discover_trlc_files(args.workspace)

    # Perform sanity checks
    results = perform_sanity_checks(trlc_files)

    # Output results in a logical format for CI like Json or similar
    print(json(results))

    # Exit with setted codes
    if ("error" in result for result in results):
        exit(1)
    else:
        exit(0)

if __name__ == "__main__":
    main()
