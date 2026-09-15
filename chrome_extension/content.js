//globals
let URL = "https://fantasy.espn.com/football/draft?"

chrome.storage.local.get(['enabled'], function (result) {
    if (result.enabled) {
        if (window.location.href.slice(0, 39) === URL) {
            document.
        }
    }
});

function update_board() {
    let sections = document.querySelectorAll('.fixedDataTableRowLayout_rowWrapper');
    sections.forEach((section) => {
        if (section.querySelector('.public_fixedDataTableCell_cellContent').textContent.trim() === 'Pick') {
        } else {
            //get name and insert
            let flex = section.querySelector('.flex');
            if (flex) {
                //get name
                let player_name = flex.querySelector('.truncate').textContent.trim();

                //get rank
                let our_rank = pull_data(player_name)
                if (!our_rank) {
                    console.log("could not get the rank")
                }

                //insert
                let new_text = document.createElement('span');
                new_text.textContent = our_rank;
                flex.appendChild(new_text);
            }
        }
    });
}

function pull_data(player_n) {
    document.getElementById('csvFile').addEventListener('change', (event) => {
        const file = event.target.files[0];
        if (file) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const text = e.target.result;

                //parse into array
                const parsedData = parseCsvString(text);
                console.log(parsedData);

                //successes message
                document.getElementById('output').textContent = "Success";

                //search logic
                for (let i = 0; i < parsedData.length; i++) {
                    let row = parsedData[i];
                    if (row.Name === player_n) {
                        return row.FantasyPros;
                    }
                }
            }
        } else {
            console.log("could not get the event file")
            return 0;
        }
    })
}

// A simple function to parse the CSV string into an array of objects
function parseCsvString(csvString) {
    const lines = csvString.split('\n').filter(line => line.trim() !== ''); // Split into rows and remove empty lines
    const headers = lines[0].split(',');
    const result = [];

    for (let i = 1; i < lines.length; i++) {
        const values = lines[i].split(',');
        if (values.length === headers.length) {
            const obj = {};
            for (let j = 0; j < headers.length; j++) {
                obj[headers[j].trim()] = values[j].trim();
            }
            result.push(obj);
        }
    }

    return result;
}