use rusqlite::Result;
use std::error::Error;

fn main() -> Result<(), Box<dyn Error>> {
    opennic_search::web::main()?;
    Ok(())
}
